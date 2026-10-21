"""Member-visible metadata catalog for uploaded document versions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from contract_app.documents.ingestion import UploadRole
from contract_app.documents.parse_errors import PARSE_ERROR_CODE_VALUES, ParseErrorCode

IndexStatus = Literal["missing", "queued", "running", "failed", "ready", "stale"]
IndexErrorCode = Literal[
    "index_media_unsupported",
    "index_storage_backend_mismatch",
    "index_rendered_source_missing",
    "index_processing_error",
    "index_commit_error",
]
SelectionStatus = Literal["not_selected", "selected", "revoked"]
AuthorizationStatus = Literal["not_authorized", "authorized", "expired"]
DocumentStatus = Literal["staged", "active", "superseded", "revoked"]
ParseStatus = Literal["queued", "processing", "completed", "needs_material", "failed"]


@dataclass(frozen=True, slots=True)
class CaseDocument:
    document_version_id: UUID
    case_id: UUID
    file_name: str
    role: UploadRole
    media_type: str
    size_bytes: int
    content_hash: str
    parse_status: ParseStatus
    parse_error_code: ParseErrorCode | None
    parser_version: str
    warnings: list[str]
    case_version: int
    document_status: DocumentStatus
    index_status: IndexStatus
    index_error_code: IndexErrorCode | None
    selection_id: UUID | None
    selection_status: SelectionStatus
    authorization_status: AuthorizationStatus
    amount_authorized: bool
    authorization_valid_until: datetime | None
    purpose_authorizations: list[dict[str, object]]
    created_at: datetime


class DocumentCatalogDenied(PermissionError):
    """Actor is not a current member permitted to view case document metadata."""


class DocumentCatalogStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def list_case_documents(
        self, tenant_id: UUID, case_id: UUID, actor_id: UUID
    ) -> list[CaseDocument]:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            member = connection.execute(
                "SELECT role FROM case_members WHERE tenant_id = %s AND case_id = %s "
                "AND actor_id = %s AND revoked_at IS NULL",
                (tenant_id, case_id, actor_id),
            ).fetchone()
            if member is None or member["role"] not in {"submitter", "reviewer"}:
                raise DocumentCatalogDenied("case_member_not_authorized")
            rows = connection.execute(
                """
                SELECT d.id AS document_version_id, d.uploaded_case_id AS case_id,
                       d.source_name AS file_name,
                       CASE d.source_kind WHEN 'main' THEN 'primary_contract'
                            WHEN 'customer' THEN 'customer_contract'
                            ELSE 'attachment' END AS role,
                       d.media_type, d.size_bytes, d.source_sha256 AS content_hash,
                       d.parse_status, d.parser_version,
                       CASE WHEN d.parse_status IN ('completed', 'needs_material')
                            THEN d.warnings ELSE '[]'::jsonb END AS warnings,
                       d.upload_case_version AS case_version, d.status AS document_status,
                       d.created_at,
                       CASE WHEN d.parse_status IN ('queued', 'processing', 'failed')
                                  AND parse_job.last_error = ANY(%s::text[])
                            THEN parse_job.last_error ELSE NULL
                       END AS parse_error_code,
                       CASE WHEN idx.document_version_id IS NOT NULL
                                  AND idx.source_sha256 = d.source_sha256 THEN 'ready'
                            WHEN idx.document_version_id IS NOT NULL THEN 'stale'
                            WHEN index_job.status IN ('queued', 'running', 'failed')
                              THEN index_job.status
                            ELSE 'missing' END AS index_status,
                       CASE WHEN idx.document_version_id IS NULL
                                  AND index_job.status = 'failed'
                                  AND index_job.last_error IN (
                                    'index_media_unsupported',
                                    'index_storage_backend_mismatch',
                                    'index_processing_error',
                                    'index_commit_error'
                                  )
                            THEN index_job.last_error ELSE NULL END AS index_error_code,
                       sel.id AS selection_id,
                       CASE WHEN sel.revoked_at IS NOT NULL THEN 'revoked'
                            WHEN sel.id IS NOT NULL THEN 'selected'
                            ELSE 'not_selected' END AS selection_status,
                       CASE WHEN sel.id IS NULL OR grant_row.id IS NULL THEN 'not_authorized'
                            WHEN grant_row.valid_until <= now() THEN 'expired'
                            WHEN NOT grant_row.allow_text THEN 'not_authorized'
                            ELSE 'authorized' END AS authorization_status,
                       COALESCE(
                         sel.id IS NOT NULL AND sel.revoked_at IS NULL
                         AND grant_row.allow_text AND grant_row.allow_amount
                         AND grant_row.valid_until > now(), false
                       ) AS amount_authorized,
                       grant_row.valid_until AS authorization_valid_until,
                       COALESCE(purpose_grants.permissions, '[]'::jsonb)
                         AS purpose_authorizations
                FROM document_versions AS d
                LEFT JOIN document_parse_jobs AS parse_job
                  ON parse_job.tenant_id = d.tenant_id
                 AND parse_job.document_version_id = d.id
                LEFT JOIN document_indexes AS idx
                  ON idx.tenant_id = d.tenant_id AND idx.document_version_id = d.id
                LEFT JOIN document_index_jobs AS index_job
                  ON index_job.tenant_id = d.tenant_id
                 AND index_job.document_version_id = d.id
                LEFT JOIN LATERAL (
                  SELECT s.id, s.revoked_at
                  FROM contract_selections AS s
                  WHERE s.tenant_id = d.tenant_id AND s.case_id = d.uploaded_case_id
                    AND s.document_version_id = d.id
                  ORDER BY (s.revoked_at IS NULL) DESC, s.selected_at DESC, s.id DESC
                  LIMIT 1
                ) AS sel ON true
                LEFT JOIN LATERAL (
                  SELECT g.id, g.allow_text, g.allow_amount, g.valid_until
                  FROM document_grants AS g
                  WHERE g.tenant_id = d.tenant_id AND g.document_version_id = d.id
                    AND g.actor_id = %s AND g.purpose = 'contract_review'
                    AND g.revoked_at IS NULL
                  LIMIT 1
                ) AS grant_row ON true
                LEFT JOIN LATERAL (
                  SELECT jsonb_agg(
                    jsonb_build_object(
                      'purpose', g.purpose,
                      'allow_text', g.allow_text,
                      'allow_amount', g.allow_amount,
                      'valid_until', g.valid_until,
                      'status', CASE WHEN g.valid_until > now()
                                     THEN 'authorized' ELSE 'expired' END
                    ) ORDER BY g.purpose
                  ) AS permissions
                  FROM document_grants AS g
                  WHERE g.tenant_id = d.tenant_id AND g.document_version_id = d.id
                    AND g.actor_id = %s AND g.revoked_at IS NULL
                ) AS purpose_grants ON true
                WHERE d.tenant_id = %s AND d.uploaded_case_id = %s
                  AND d.upload_key IS NOT NULL AND d.removed_at IS NULL
                ORDER BY d.created_at DESC, d.id
                """,
                (list(PARSE_ERROR_CODE_VALUES), actor_id, actor_id, tenant_id, case_id),
            ).fetchall()
        return [CaseDocument(**row) for row in rows]
