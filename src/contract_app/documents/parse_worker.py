"""Recoverable worker for parsing immutable document uploads."""

import hashlib
import os
import signal
import socket
from collections.abc import Callable
from threading import Event, Thread
from time import perf_counter, sleep
from uuid import UUID

from contract_app.documents.artifacts import serialize_parsed_document
from contract_app.documents.docx_render import DocxRenderError, RenderedDocx, render_docx_to_pdf
from contract_app.documents.object_locking import object_key_locks
from contract_app.documents.parse_errors import normalize_parse_error_code
from contract_app.documents.parse_jobs import (
    DocumentParseJobStore,
    LeasedDocumentParseJob,
    ParseJobOutcome,
)
from contract_app.documents.parser import DOCX_MEDIA_TYPE, ParseError, parse_document
from contract_app.documents.storage import ObjectStore, create_object_store
from contract_app.telemetry import configure_from_environment, emit_operation, shutdown_telemetry


class DocumentParseWorker:
    def __init__(
        self,
        store: DocumentParseJobStore,
        tenant_id: UUID,
        worker_id: str,
        object_store: ObjectStore,
        *,
        docx_renderer: Callable[[bytes], RenderedDocx] = render_docx_to_pdf,
        lease_seconds: int = 900,
        max_attempts: int = 3,
        retry_delay_seconds: int = 30,
    ) -> None:
        if not worker_id or lease_seconds <= 0 or max_attempts <= 0 or retry_delay_seconds < 0:
            raise ValueError("invalid_document_parse_worker_configuration")
        self.store = store
        self.tenant_id = tenant_id
        self.worker_id = worker_id
        self.object_store = object_store
        self.docx_renderer = docx_renderer
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds

    def run_once(self) -> ParseJobOutcome:
        started = perf_counter()
        outcome: ParseJobOutcome = "failed"
        job: LeasedDocumentParseJob | None = None
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
            emit_operation("document.parse", **fields)

    def _run_once(self, job: LeasedDocumentParseJob | None) -> ParseJobOutcome:
        if job is None:
            return "idle"
        heartbeat_stop = Event()
        lease_lost = Event()

        def pulse() -> None:
            while not heartbeat_stop.wait(min(60.0, self.lease_seconds / 3)):
                try:
                    renewed = self.store.heartbeat(
                        self.tenant_id, job, self.worker_id, self.lease_seconds
                    )
                except Exception:
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    return

        heartbeat = Thread(target=pulse, name="document-parse-heartbeat", daemon=True)
        heartbeat.start()
        try:
            if job.storage_backend != self.object_store.backend:
                heartbeat_stop.set()
                heartbeat.join()
                return self.store.fail(
                    self.tenant_id, job, self.worker_id, "parse_storage_mismatch"
                )
            data = self.object_store.get(job.object_ref)
            rendered = self.docx_renderer(data) if job.media_type == DOCX_MEDIA_TYPE else None
            parsed = parse_document(data, job.media_type, rendered_docx=rendered)
            artifact = serialize_parsed_document(parsed)
            rendered_digest = (
                hashlib.sha256(rendered.pdf_bytes).hexdigest() if rendered is not None else None
            )
            with object_key_locks(
                self.store.database_url,
                self.object_store.backend,
                self.tenant_id,
                job.case_id,
                {
                    job.source_sha256,
                    hashlib.sha256(artifact).hexdigest(),
                    *([rendered_digest] if rendered_digest is not None else []),
                },
            ):
                rendered_ref = (
                    self.object_store.put(self.tenant_id, job.case_id, rendered.pdf_bytes)
                    if rendered is not None
                    else None
                )
                parsed_ref = self.object_store.put(self.tenant_id, job.case_id, artifact)
                heartbeat_stop.set()
                heartbeat.join()
                if lease_lost.is_set():
                    return "lease_lost"
                return (
                    "completed"
                    if self.store.complete(
                        self.tenant_id, job, self.worker_id, parsed, parsed_ref, rendered_ref
                    )
                    else "lease_lost"
                )
        except ParseError as exc:
            heartbeat_stop.set()
            heartbeat.join()
            if lease_lost.is_set():
                return "lease_lost"
            return self.store.fail(
                self.tenant_id,
                job,
                self.worker_id,
                normalize_parse_error_code(str(exc)),
            )
        except DocxRenderError as exc:
            heartbeat_stop.set()
            heartbeat.join()
            if lease_lost.is_set():
                return "lease_lost"
            return self.store.retry_or_fail(
                self.tenant_id,
                job,
                self.worker_id,
                max_attempts=self.max_attempts,
                retry_delay_seconds=self.retry_delay_seconds,
                error_code=normalize_parse_error_code(str(exc)),
            )
        except Exception:
            heartbeat_stop.set()
            heartbeat.join()
            if lease_lost.is_set():
                return "lease_lost"
            return self.store.retry_or_fail(
                self.tenant_id,
                job,
                self.worker_id,
                max_attempts=self.max_attempts,
                retry_delay_seconds=self.retry_delay_seconds,
                error_code="parse_processing_error",
            )


def main() -> None:
    configure_from_environment("contract-audit-parse-worker")
    try:
        database_url = os.environ.get("DATABASE_URL")
        tenant_value = os.environ.get("DOCUMENT_PARSE_TENANT_ID")
        if not database_url or not tenant_value:
            raise SystemExit("DATABASE_URL and DOCUMENT_PARSE_TENANT_ID are required")
        tenant_id = UUID(tenant_value)
        worker_id = os.environ.get(
            "DOCUMENT_PARSE_WORKER_ID", f"document-parse-{socket.gethostname()}-{os.getpid()}"
        )
        worker = DocumentParseWorker(
            DocumentParseJobStore(database_url), tenant_id, worker_id, create_object_store()
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
