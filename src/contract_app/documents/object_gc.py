"""Tenant-scoped, age-gated cleanup of unreferenced immutable document objects."""

import logging
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from contract_app.documents.object_locking import object_key_locks
from contract_app.documents.storage import ObjectStore, create_object_store

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ObjectSweepResult:
    scanned: int
    deleted: int
    retained: int
    already_missing: int


class DocumentObjectGarbageCollector:
    def __init__(
        self,
        database_url: str,
        tenant_id: UUID,
        object_store: ObjectStore,
        *,
        minimum_age: timedelta = timedelta(days=1),
    ) -> None:
        if not database_url or minimum_age <= timedelta(0):
            raise ValueError("document_object_gc_configuration_invalid")
        self.database_url = database_url
        self.tenant_id = tenant_id
        self.object_store = object_store
        self.minimum_age = minimum_age

    def sweep_once(self, *, now: datetime | None = None) -> ObjectSweepResult:
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            raise ValueError("gc_time_must_be_timezone_aware")
        cutoff = current_time.astimezone(UTC) - self.minimum_age
        scanned = 0
        deleted = 0
        retained = 0
        already_missing = 0
        for candidate in self.object_store.iter_before(self.tenant_id, cutoff):
            scanned += 1
            reference = candidate.reference
            with object_key_locks(
                self.database_url,
                self.object_store.backend,
                reference.tenant_id,
                reference.case_id,
                {reference.sha256},
            ):
                with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                    connection.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (str(self.tenant_id),),
                    )
                    reference_row = connection.execute(
                        "SELECT EXISTS (SELECT 1 FROM document_versions "
                        "WHERE tenant_id = %s AND uploaded_case_id = %s "
                        "AND removed_at IS NULL "
                        "AND storage_backend = %s AND (source_sha256 = %s "
                        "OR parsed_sha256 = %s OR rendered_pdf_sha256 = %s)) AS is_referenced",
                        (
                            self.tenant_id,
                            reference.case_id,
                            self.object_store.backend,
                            reference.sha256,
                            reference.sha256,
                            reference.sha256,
                        ),
                    ).fetchone()
                if reference_row is not None and reference_row["is_referenced"]:
                    retained += 1
                elif self.object_store.delete(reference):
                    deleted += 1
                else:
                    already_missing += 1
        for temporary in self.object_store.iter_temporary_before(self.tenant_id, cutoff):
            scanned += 1
            with object_key_locks(
                self.database_url,
                self.object_store.backend,
                temporary.tenant_id,
                temporary.case_id,
                {temporary.sha256},
            ):
                if self.object_store.delete_temporary(temporary, cutoff):
                    deleted += 1
                else:
                    already_missing += 1
        return ObjectSweepResult(scanned, deleted, retained, already_missing)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ.get("DATABASE_URL")
    tenant_value = os.environ.get("DOCUMENT_GC_TENANT_ID")
    if not database_url or not tenant_value:
        raise SystemExit("DATABASE_URL and DOCUMENT_GC_TENANT_ID are required")
    interval_seconds = int(os.environ.get("DOCUMENT_GC_INTERVAL_SECONDS", "3600"))
    minimum_age_seconds = int(os.environ.get("DOCUMENT_GC_MINIMUM_AGE_SECONDS", "86400"))
    if interval_seconds <= 0 or minimum_age_seconds <= 0:
        raise SystemExit("document GC intervals must be positive")
    collector = DocumentObjectGarbageCollector(
        database_url,
        UUID(tenant_value),
        create_object_store(),
        minimum_age=timedelta(seconds=minimum_age_seconds),
    )
    stopping = Event()

    def stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping.is_set():
        result = collector.sweep_once()
        LOGGER.info(
            "document object sweep completed scanned=%d deleted=%d retained=%d missing=%d",
            result.scanned,
            result.deleted,
            result.retained,
            result.already_missing,
        )
        stopping.wait(interval_seconds)


if __name__ == "__main__":
    main()
