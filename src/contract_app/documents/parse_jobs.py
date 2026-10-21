"""Leased, tenant-scoped document parsing jobs."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from contract_app.documents.models import ParsedDocument
from contract_app.documents.parse_errors import normalize_parse_error_code
from contract_app.documents.parser import DOCX_MEDIA_TYPE
from contract_app.documents.storage import ObjectRef

ParseJobOutcome = Literal["idle", "completed", "queued", "failed", "lease_lost"]


@dataclass(frozen=True, slots=True)
class LeasedDocumentParseJob:
    id: UUID
    tenant_id: UUID
    case_id: UUID
    document_version_id: UUID
    source_sha256: str
    size_bytes: int
    storage_backend: str
    media_type: str
    attempt: int
    fencing_token: int
    lease_until: datetime
    queue_wait_ms: float = 0.0

    @property
    def object_ref(self) -> ObjectRef:
        return ObjectRef(self.tenant_id, self.case_id, self.source_sha256, self.size_bytes)


class DocumentParseJobStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    @property
    def database_url(self) -> str:
        return self._database_url

    def _connect(self, tenant_id: UUID) -> psycopg.Connection[Any]:
        connection: psycopg.Connection[Any] = psycopg.connect(
            self._database_url, row_factory=dict_row
        )
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return connection

    def claim(
        self, tenant_id: UUID, worker_id: str, lease_seconds: int = 900
    ) -> LeasedDocumentParseJob | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("invalid_parse_worker_lease")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                WITH candidate AS (
                  SELECT j.id,
                    (EXTRACT(EPOCH FROM (clock_timestamp() -
                      GREATEST(j.next_run_at, COALESCE(j.lease_until, j.created_at)))) * 1000)
                      ::double precision AS queue_wait_ms
                  FROM document_parse_jobs AS j
                  JOIN document_versions AS d
                    ON d.tenant_id = j.tenant_id AND d.id = j.document_version_id
                  WHERE j.next_run_at <= now()
                    AND (j.status = 'queued' OR
                         (j.status = 'running' AND j.lease_until < now()))
                    AND d.parse_status IN ('queued', 'processing')
                    AND d.status IN ('staged', 'active')
                  ORDER BY j.next_run_at, j.created_at, j.id
                  FOR UPDATE OF j SKIP LOCKED LIMIT 1
                )
                UPDATE document_parse_jobs AS j
                SET status = 'running', attempt = j.attempt + 1,
                    lease_owner = %s, lease_until = now() + make_interval(secs => %s),
                    fencing_token = j.fencing_token + 1, updated_at = now()
                FROM candidate, document_versions AS d
                WHERE j.id = candidate.id AND j.tenant_id = %s
                  AND d.tenant_id = j.tenant_id AND d.id = j.document_version_id
                RETURNING j.id, j.tenant_id, j.case_id, j.document_version_id,
                          j.source_sha256, j.attempt, j.fencing_token, j.lease_until,
                          d.size_bytes, d.storage_backend, d.media_type,
                          candidate.queue_wait_ms
                """,
                (worker_id, lease_seconds, tenant_id),
            ).fetchone()
            if row is not None:
                connection.execute(
                    "UPDATE document_versions SET parse_status = 'processing' "
                    "WHERE tenant_id = %s AND id = %s "
                    "AND parse_status IN ('queued', 'processing')",
                    (tenant_id, row["document_version_id"]),
                )
        return LeasedDocumentParseJob(**row) if row is not None else None

    def heartbeat(
        self,
        tenant_id: UUID,
        job: LeasedDocumentParseJob,
        worker_id: str,
        lease_seconds: int = 900,
    ) -> bool:
        if lease_seconds <= 0:
            raise ValueError("invalid_parse_worker_lease")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                "UPDATE document_parse_jobs SET lease_until = now() + make_interval(secs => %s), "
                "updated_at = now() WHERE tenant_id = %s AND id = %s AND status = 'running' "
                "AND lease_owner = %s AND fencing_token = %s AND lease_until > now() RETURNING id",
                (lease_seconds, tenant_id, job.id, worker_id, job.fencing_token),
            ).fetchone()
        return row is not None

    def complete(
        self,
        tenant_id: UUID,
        job: LeasedDocumentParseJob,
        worker_id: str,
        parsed: ParsedDocument,
        parsed_ref: ObjectRef,
        rendered_ref: ObjectRef | None = None,
    ) -> bool:
        if parsed.source_sha256 != job.source_sha256 or parsed.media_type != job.media_type:
            raise ValueError("parsed_document_identity_mismatch")
        if job.media_type == DOCX_MEDIA_TYPE:
            if (
                rendered_ref is None
                or parsed.index_source_sha256 != rendered_ref.sha256
                or parsed.renderer_identity is None
            ):
                raise ValueError("docx_render_identity_missing")
        elif (
            rendered_ref is not None
            or parsed.index_source_sha256 != job.source_sha256
            or parsed.renderer_identity is not None
        ):
            raise ValueError("pdf_render_identity_invalid")
        warnings = list(parsed.warnings)
        status = (
            "needs_material"
            if any(
                warning.startswith(("page_", "docx_has_no_text"))
                and warning != "docx_has_no_stable_page_coordinates"
                for warning in warnings
            )
            else "completed"
        )
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE document_parse_jobs AS j
                SET status = 'completed', lease_owner = NULL, lease_until = NULL,
                    last_error = NULL, updated_at = now()
                WHERE j.tenant_id = %s AND j.id = %s AND j.status = 'running'
                  AND j.lease_owner = %s AND j.fencing_token = %s AND j.lease_until > now()
                  AND EXISTS (SELECT 1 FROM document_versions AS d
                    WHERE d.tenant_id = j.tenant_id AND d.id = j.document_version_id
                      AND d.source_sha256 = j.source_sha256 AND d.status IN ('staged', 'active'))
                RETURNING j.case_id, j.document_version_id, j.source_sha256
                """,
                (tenant_id, job.id, worker_id, job.fencing_token),
            ).fetchone()
            if row is None:
                return False
            updated = connection.execute(
                "UPDATE document_versions SET parse_status = %s, parser_version = %s, "
                "parsed_sha256 = %s, parsed_size_bytes = %s, warnings = %s::jsonb, "
                "rendered_pdf_sha256 = %s, rendered_pdf_size_bytes = %s, renderer_identity = %s "
                "WHERE tenant_id = %s AND id = %s AND source_sha256 = %s "
                "AND status IN ('staged', 'active') RETURNING id",
                (
                    status,
                    parsed.parser_version,
                    parsed_ref.sha256,
                    parsed_ref.size_bytes,
                    json.dumps(warnings),
                    rendered_ref.sha256 if rendered_ref is not None else None,
                    rendered_ref.size_bytes if rendered_ref is not None else None,
                    parsed.renderer_identity,
                    tenant_id,
                    row["document_version_id"],
                    row["source_sha256"],
                ),
            ).fetchone()
            if updated is None:
                raise RuntimeError("parse_document_version_changed")
            connection.execute(
                "INSERT INTO document_index_jobs "
                "(tenant_id, case_id, document_version_id, source_sha256) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, document_version_id) DO NOTHING",
                (tenant_id, row["case_id"], row["document_version_id"], row["source_sha256"]),
            )
        return True

    def fail(
        self,
        tenant_id: UUID,
        job: LeasedDocumentParseJob,
        worker_id: str,
        error_code: str,
    ) -> Literal["failed", "lease_lost"]:
        error_code = normalize_parse_error_code(error_code)
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                "UPDATE document_parse_jobs SET status = 'failed', lease_owner = NULL, "
                "lease_until = NULL, last_error = %s, updated_at = now() "
                "WHERE tenant_id = %s AND id = %s AND status = 'running' "
                "AND lease_owner = %s AND fencing_token = %s AND lease_until > now() RETURNING id",
                (error_code, tenant_id, job.id, worker_id, job.fencing_token),
            ).fetchone()
            if row is None:
                return "lease_lost"
            connection.execute(
                "UPDATE document_versions SET parse_status = 'failed', parser_version = %s, "
                "parsed_sha256 = NULL, parsed_size_bytes = NULL, warnings = %s::jsonb "
                "WHERE tenant_id = %s AND id = %s AND source_sha256 = %s",
                (
                    "failed",
                    json.dumps([error_code]),
                    tenant_id,
                    job.document_version_id,
                    job.source_sha256,
                ),
            )
        return "failed"

    def retry_or_fail(
        self,
        tenant_id: UUID,
        job: LeasedDocumentParseJob,
        worker_id: str,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: int = 30,
        error_code: str = "parse_processing_error",
    ) -> ParseJobOutcome:
        error_code = normalize_parse_error_code(error_code)
        status = "failed" if job.attempt >= max_attempts else "queued"
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                "UPDATE document_parse_jobs SET status = %s, lease_owner = NULL, "
                "lease_until = NULL, next_run_at = now() + make_interval(secs => %s), "
                "last_error = %s, updated_at = now() "
                "WHERE tenant_id = %s AND id = %s AND status = 'running' "
                "AND lease_owner = %s AND fencing_token = %s AND lease_until > now() RETURNING id",
                (
                    status,
                    retry_delay_seconds,
                    error_code,
                    tenant_id,
                    job.id,
                    worker_id,
                    job.fencing_token,
                ),
            ).fetchone()
            if row is None:
                return "lease_lost"
            if status == "failed":
                connection.execute(
                    "UPDATE document_versions SET parse_status = 'failed', "
                    "parser_version = 'failed', "
                    "warnings = %s::jsonb "
                    "WHERE tenant_id = %s AND id = %s AND source_sha256 = %s",
                    (
                        json.dumps([error_code]),
                        tenant_id,
                        job.document_version_id,
                        job.source_sha256,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE document_versions SET parse_status = 'queued' "
                    "WHERE tenant_id = %s AND id = %s AND source_sha256 = %s "
                    "AND parse_status = 'processing'",
                    (tenant_id, job.document_version_id, job.source_sha256),
                )
        return cast(ParseJobOutcome, status)
