"""Document-index queue worker; its PageIndex state is derived from immutable uploads."""

import hashlib
import os
import signal
import socket
import tempfile
from pathlib import Path
from threading import Event, Thread
from time import perf_counter, sleep
from uuid import UUID

from contract_app.documents.index_jobs import (
    DocumentIndexJobStore,
    IndexJobOutcome,
    LeasedDocumentIndexJob,
)
from contract_app.documents.storage import ObjectStore, create_object_store
from contract_app.retrieval.pageindex_adapter import (
    PageIndexAdapter,
    RetrievalScope,
    local_pageindex_adapter,
)
from contract_app.telemetry import configure_from_environment, emit_operation, shutdown_telemetry


class DocumentIndexWorker:
    def __init__(
        self,
        store: DocumentIndexJobStore,
        tenant_id: UUID,
        worker_id: str,
        object_store: ObjectStore,
        pageindex: PageIndexAdapter,
        *,
        lease_seconds: int = 900,
        max_attempts: int = 3,
        retry_delay_seconds: int = 30,
    ) -> None:
        if not worker_id or lease_seconds <= 0 or max_attempts <= 0 or retry_delay_seconds < 0:
            raise ValueError("invalid_document_index_worker_configuration")
        self.store = store
        self.tenant_id = tenant_id
        self.worker_id = worker_id
        self.object_store = object_store
        self.pageindex = pageindex
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds

    def run_once(self) -> IndexJobOutcome:
        started = perf_counter()
        outcome: IndexJobOutcome = "failed"
        job: LeasedDocumentIndexJob | None = None
        try:
            job = self.store.claim(self.tenant_id, self.worker_id, self.lease_seconds)
            outcome = self._run_once(job)
            return outcome
        finally:
            fields: dict[str, str | int | float] = {
                "outcome": outcome,
                "duration_ms": (perf_counter() - started) * 1000,
            }
            if job is not None:
                fields["queue_wait_ms"] = job.queue_wait_ms
            emit_operation("document.index", **fields)

    def _run_once(self, job: LeasedDocumentIndexJob | None) -> IndexJobOutcome:
        if job is None:
            return "idle"
        heartbeat_stop = Event()
        lease_lost = Event()
        interval = min(60.0, self.lease_seconds / 3)

        def pulse() -> None:
            while not heartbeat_stop.wait(interval):
                try:
                    renewed = self.store.heartbeat(
                        self.tenant_id, job, self.worker_id, self.lease_seconds
                    )
                except Exception:
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    return

        heartbeat = Thread(target=pulse, name="document-index-heartbeat", daemon=True)
        heartbeat.start()
        pageindex_document_id: str | None = None
        error_code: str | None = None
        retryable = True
        try:
            if job.storage_backend != self.object_store.backend:
                error_code = "index_storage_backend_mismatch"
                retryable = False
            else:
                try:
                    index_ref = job.index_object_ref
                except ValueError:
                    error_code = "index_rendered_source_missing"
                    retryable = False
                    index_ref = None
                if index_ref is not None:
                    data = self.object_store.get(index_ref)
                    index_sha256 = index_ref.sha256
                    if hashlib.sha256(data).hexdigest() != index_sha256:
                        raise ValueError("index_source_hash_mismatch")
                else:
                    data = b""
                    index_sha256 = ""
                if error_code is None:
                    with tempfile.TemporaryDirectory(prefix="contract-index-") as directory:
                        path = Path(directory) / "source.pdf"
                        path.write_bytes(data)
                        indexed = self.pageindex.index_pdf(
                            RetrievalScope(
                                self.tenant_id,
                                job.case_id,
                                "contract_review",
                                frozenset({job.document_version_id}),
                            ),
                            self.tenant_id,
                            job.case_id,
                            job.document_version_id,
                            job.source_sha256,
                            path,
                            index_sha256=index_sha256,
                        )
                        pageindex_document_id = indexed.pageindex_doc_id
        except Exception:
            # Keep raw SDK/object-store exceptions out of persisted task metadata.
            error_code = "index_processing_error"
        finally:
            heartbeat_stop.set()
            heartbeat.join()
        if lease_lost.is_set():
            return "lease_lost"
        if error_code is not None:
            return self.store.retry_or_fail(
                self.tenant_id,
                job,
                self.worker_id,
                error_code,
                retryable=retryable,
                max_attempts=self.max_attempts,
                retry_delay_seconds=self.retry_delay_seconds,
            )
        assert pageindex_document_id is not None
        try:
            completed = self.store.complete(
                self.tenant_id,
                job,
                self.worker_id,
                pageindex_document_id,
                self.pageindex.configuration_snapshot,
                job.rendered_pdf_sha256,
                job.renderer_identity,
            )
        except Exception:
            return self.store.retry_or_fail(
                self.tenant_id,
                job,
                self.worker_id,
                "index_commit_error",
                max_attempts=self.max_attempts,
                retry_delay_seconds=self.retry_delay_seconds,
            )
        return "completed" if completed else "lease_lost"


def main() -> None:
    configure_from_environment("contract-audit-index-worker")
    try:
        database_url = os.environ.get("DATABASE_URL")
        tenant_value = os.environ.get("DOCUMENT_INDEX_TENANT_ID")
        model = os.environ.get("PAGEINDEX_MODEL")
        if not database_url or not tenant_value or not model:
            raise SystemExit(
                "DATABASE_URL, DOCUMENT_INDEX_TENANT_ID and PAGEINDEX_MODEL are required"
            )
        tenant_id = UUID(tenant_value)
        worker_id = os.environ.get(
            "DOCUMENT_INDEX_WORKER_ID", f"document-index-{socket.gethostname()}-{os.getpid()}"
        )
        storage_path = Path(os.environ.get("PAGEINDEX_STORAGE_PATH", "./var/pageindex"))
        worker = DocumentIndexWorker(
            DocumentIndexJobStore(database_url),
            tenant_id,
            worker_id,
            create_object_store(),
            local_pageindex_adapter(storage_path, model),
        )
        stopping = Event()
        signal.signal(signal.SIGINT, lambda _signum, _frame: stopping.set())
        signal.signal(signal.SIGTERM, lambda _signum, _frame: stopping.set())
        while not stopping.is_set():
            if worker.run_once() == "idle":
                sleep(2)
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    main()
