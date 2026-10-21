"""Leased tenant-scoped jobs for building immutable PageIndex document entries."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from contract_app.documents.storage import ObjectRef

IndexJobOutcome = Literal["idle", "completed", "retried", "failed", "lease_lost"]


@dataclass(frozen=True, slots=True)
class LeasedDocumentIndexJob:
    id: UUID
    tenant_id: UUID
    case_id: UUID
    document_version_id: UUID
    source_sha256: str
    size_bytes: int
    storage_backend: str
    media_type: str
    rendered_pdf_sha256: str | None
    rendered_pdf_size_bytes: int | None
    renderer_identity: str | None
    attempt: int
    fencing_token: int
    lease_until: datetime
    queue_wait_ms: float = 0.0

    @property
    def object_ref(self) -> ObjectRef:
        return ObjectRef(self.tenant_id, self.case_id, self.source_sha256, self.size_bytes)

    @property
    def index_object_ref(self) -> ObjectRef:
        if self.media_type == "application/pdf":
            return self.object_ref
        if self.rendered_pdf_sha256 is None or self.rendered_pdf_size_bytes is None:
            raise ValueError("docx_render_not_available")
        return ObjectRef(
            self.tenant_id, self.case_id, self.rendered_pdf_sha256, self.rendered_pdf_size_bytes
        )


class DocumentIndexJobStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self, tenant_id: UUID) -> psycopg.Connection[Any]:
        connection: psycopg.Connection[Any] = psycopg.connect(
            self._database_url, row_factory=dict_row
        )
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return connection

    def claim(
        self, tenant_id: UUID, worker_id: str, lease_seconds: int = 900
    ) -> LeasedDocumentIndexJob | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("invalid_index_worker_lease")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                WITH candidate AS (
                  SELECT j.id,
                    (EXTRACT(EPOCH FROM (clock_timestamp() -
                      GREATEST(j.next_run_at, COALESCE(j.lease_until, j.created_at)))) * 1000)
                      ::double precision AS queue_wait_ms
                  FROM document_index_jobs AS j
                  JOIN document_versions AS d
                    ON d.tenant_id = j.tenant_id AND d.id = j.document_version_id
                  WHERE j.next_run_at <= now()
                    AND (j.status = 'queued' OR
                         (j.status = 'running' AND j.lease_until < now()))
                    AND d.status IN ('staged', 'active')
                    AND d.parse_status IN ('completed', 'needs_material')
                  ORDER BY j.next_run_at, j.created_at, j.id
                  FOR UPDATE OF j SKIP LOCKED LIMIT 1
                )
                UPDATE document_index_jobs AS j
                SET status = 'running', attempt = j.attempt + 1,
                    lease_owner = %s, lease_until = now() + make_interval(secs => %s),
                    fencing_token = j.fencing_token + 1, updated_at = now()
                FROM candidate, document_versions AS d
                WHERE j.id = candidate.id AND j.tenant_id = %s
                  AND d.tenant_id = j.tenant_id AND d.id = j.document_version_id
                RETURNING j.id, j.tenant_id, j.case_id, j.document_version_id,
                          j.source_sha256, j.attempt, j.fencing_token, j.lease_until,
                          d.size_bytes, d.storage_backend, d.media_type,
                          d.rendered_pdf_sha256, d.rendered_pdf_size_bytes,
                          d.renderer_identity, candidate.queue_wait_ms
                """,
                (worker_id, lease_seconds, tenant_id),
            ).fetchone()
        return LeasedDocumentIndexJob(**row) if row is not None else None

    def heartbeat(
        self,
        tenant_id: UUID,
        job: LeasedDocumentIndexJob,
        worker_id: str,
        lease_seconds: int = 900,
    ) -> bool:
        if lease_seconds <= 0:
            raise ValueError("invalid_index_worker_lease")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE document_index_jobs
                SET lease_until = now() + make_interval(secs => %s), updated_at = now()
                WHERE tenant_id = %s AND id = %s AND status = 'running'
                  AND lease_owner = %s AND fencing_token = %s AND lease_until > now()
                RETURNING id
                """,
                (lease_seconds, tenant_id, job.id, worker_id, job.fencing_token),
            ).fetchone()
        return row is not None

    def complete(
        self,
        tenant_id: UUID,
        job: LeasedDocumentIndexJob,
        worker_id: str,
        pageindex_doc_id: str,
        index_configuration: dict[str, Any] | None = None,
        rendered_pdf_sha256: str | None = None,
        renderer_identity: str | None = None,
    ) -> bool:
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,255}", pageindex_doc_id) is None:
            raise ValueError("pageindex_document_id_invalid")
        if index_configuration is None:
            unversioned = {"provider": "custom", "configuration_status": "unversioned"}
            unversioned_json = json.dumps(
                unversioned, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            index_configuration = {
                "configuration": unversioned,
                "configuration_sha256": sha256(unversioned_json.encode("utf-8")).hexdigest(),
            }
        configuration = index_configuration.get("configuration")
        configuration_sha256 = index_configuration.get("configuration_sha256")
        try:
            serialized_configuration = json.dumps(
                configuration, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("pageindex_configuration_invalid") from exc
        if (
            not isinstance(configuration, dict)
            or not isinstance(configuration_sha256, str)
            or sha256(serialized_configuration.encode("utf-8")).hexdigest() != configuration_sha256
        ):
            raise ValueError("pageindex_configuration_hash_mismatch")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE document_index_jobs AS j
                SET status = 'completed', lease_owner = NULL, lease_until = NULL,
                    last_error = NULL, updated_at = now()
                WHERE j.tenant_id = %s AND j.id = %s AND j.status = 'running'
                  AND j.lease_owner = %s AND j.fencing_token = %s AND j.lease_until > now()
                  AND EXISTS (
                    SELECT 1 FROM document_versions AS d
                    WHERE d.tenant_id = j.tenant_id AND d.id = j.document_version_id
                      AND d.source_sha256 = j.source_sha256 AND d.status IN ('staged', 'active')
                      AND d.rendered_pdf_sha256 IS NOT DISTINCT FROM %s
                      AND d.renderer_identity IS NOT DISTINCT FROM %s
                  )
                RETURNING j.case_id, j.document_version_id, j.source_sha256
                """,
                (
                    tenant_id,
                    job.id,
                    worker_id,
                    job.fencing_token,
                    rendered_pdf_sha256,
                    renderer_identity,
                ),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "INSERT INTO document_indexes "
                "(tenant_id, document_version_id, source_sha256, pageindex_doc_id, "
                "pageindex_configuration, pageindex_configuration_sha256, "
                "rendered_pdf_sha256, renderer_identity) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s) "
                "ON CONFLICT (tenant_id, document_version_id) DO UPDATE SET "
                "pageindex_doc_id = EXCLUDED.pageindex_doc_id, "
                "pageindex_configuration = EXCLUDED.pageindex_configuration, "
                "pageindex_configuration_sha256 = EXCLUDED.pageindex_configuration_sha256, "
                "rendered_pdf_sha256 = EXCLUDED.rendered_pdf_sha256, "
                "renderer_identity = EXCLUDED.renderer_identity",
                (
                    tenant_id,
                    row["document_version_id"],
                    row["source_sha256"],
                    pageindex_doc_id,
                    json.dumps(index_configuration["configuration"], ensure_ascii=False),
                    index_configuration["configuration_sha256"],
                    rendered_pdf_sha256,
                    renderer_identity,
                ),
            )
        return True

    def retry_or_fail(
        self,
        tenant_id: UUID,
        job: LeasedDocumentIndexJob,
        worker_id: str,
        error_code: str,
        *,
        retryable: bool = True,
        max_attempts: int = 3,
        retry_delay_seconds: int = 30,
    ) -> Literal["retried", "failed", "lease_lost"]:
        if (
            re.fullmatch(r"[a-z][a-z0-9_]{0,127}", error_code) is None
            or max_attempts <= 0
            or retry_delay_seconds < 0
        ):
            raise ValueError("invalid_index_job_failure")
        status = "failed" if not retryable or job.attempt >= max_attempts else "queued"
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE document_index_jobs
                SET status = %s, lease_owner = NULL, lease_until = NULL,
                    next_run_at = now() + make_interval(secs => %s),
                    last_error = %s, updated_at = now()
                WHERE tenant_id = %s AND id = %s AND status = 'running'
                  AND lease_owner = %s AND fencing_token = %s AND lease_until > now()
                RETURNING id
                """,
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
        return cast(Literal["retried", "failed"], status)
