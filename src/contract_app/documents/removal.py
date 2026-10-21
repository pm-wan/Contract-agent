"""Audited removal of an uploaded material from a case."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class DocumentRemovalDenied(PermissionError):
    """Actor is not allowed to remove this uploaded document."""


class DocumentRemovalConflict(ValueError):
    """Removal conflicts with the case or document's current state."""


class DocumentRemovalInvalid(ValueError):
    """Removal request is invalid."""


@dataclass(frozen=True, slots=True)
class RemovedDocument:
    document_version_id: UUID
    case_id: UUID
    case_version: int
    removed_at: datetime
    revoked_selection_count: int
    revoked_grant_count: int
    invalidated_run_count: int


class DocumentRemovalStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def remove(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        document_version_id: UUID,
        expected_case_version: int,
        reason: str,
        idempotency_key: str,
    ) -> RemovedDocument:
        normalized_reason = reason.strip()
        if (
            expected_case_version <= 0
            or not normalized_reason
            or len(normalized_reason) > 500
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None
        ):
            raise DocumentRemovalInvalid("document_removal_request_invalid")

        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            case = connection.execute(
                "SELECT version FROM audit_cases WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, case_id),
            ).fetchone()
            member = connection.execute(
                "SELECT role FROM case_members WHERE tenant_id = %s AND case_id = %s "
                "AND actor_id = %s AND revoked_at IS NULL",
                (tenant_id, case_id, actor_id),
            ).fetchone()
            if case is None or member is None or member["role"] not in {"submitter", "reviewer"}:
                raise DocumentRemovalDenied("case_member_required")

            prior = connection.execute(
                "SELECT * FROM document_removal_events WHERE tenant_id = %s "
                "AND idempotency_key = %s",
                (tenant_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if (
                    prior["case_id"] != case_id
                    or prior["document_version_id"] != document_version_id
                    or prior["actor_id"] != actor_id
                ):
                    raise DocumentRemovalConflict("idempotency_key_conflict")
                return RemovedDocument(
                    document_version_id=document_version_id,
                    case_id=case_id,
                    case_version=int(prior["case_version"]),
                    removed_at=prior["removed_at"],
                    revoked_selection_count=len(prior["revoked_selection_ids"]),
                    revoked_grant_count=len(prior["revoked_grant_ids"]),
                    invalidated_run_count=len(prior["invalidated_run_ids"]),
                )

            if int(case["version"]) != expected_case_version:
                raise DocumentRemovalConflict("case_version_changed")

            document = connection.execute(
                "SELECT status, removed_at FROM document_versions "
                "WHERE tenant_id = %s AND uploaded_case_id = %s AND id = %s "
                "AND upload_key IS NOT NULL FOR UPDATE",
                (tenant_id, case_id, document_version_id),
            ).fetchone()
            if document is None:
                raise DocumentRemovalDenied("document_not_available")
            if document["removed_at"] is not None:
                raise DocumentRemovalConflict("document_already_removed")
            if document["status"] not in {"staged", "active", "superseded", "revoked"}:
                raise DocumentRemovalConflict("document_not_removable")

            selections = connection.execute(
                "SELECT id FROM contract_selections WHERE tenant_id = %s AND case_id = %s "
                "AND document_version_id = %s AND revoked_at IS NULL FOR UPDATE",
                (tenant_id, case_id, document_version_id),
            ).fetchall()
            selection_ids = [row["id"] for row in selections]
            grants = connection.execute(
                "SELECT id, actor_id, purpose, allow_text, allow_amount, valid_until "
                "FROM document_grants WHERE tenant_id = %s AND document_version_id = %s "
                "AND revoked_at IS NULL FOR UPDATE",
                (tenant_id, document_version_id),
            ).fetchall()
            grant_ids = [row["id"] for row in grants]
            permission_snapshot = [
                {
                    "actor_id": str(grant["actor_id"]),
                    "purpose": grant["purpose"],
                    "allow_text": grant["allow_text"],
                    "allow_amount": grant["allow_amount"],
                    "valid_until": grant["valid_until"].isoformat(),
                }
                for grant in grants
            ]
            removed_at = connection.execute(
                "SELECT clock_timestamp() AS removed_at"
            ).fetchone()["removed_at"]

            connection.execute(
                "UPDATE contract_selections SET revoked_at = %s WHERE tenant_id = %s "
                "AND case_id = %s AND document_version_id = %s AND revoked_at IS NULL",
                (removed_at, tenant_id, case_id, document_version_id),
            )
            connection.execute(
                "UPDATE document_grants SET revoked_at = %s WHERE tenant_id = %s "
                "AND document_version_id = %s AND revoked_at IS NULL",
                (removed_at, tenant_id, document_version_id),
            )

            invalidated_rows = connection.execute(
                """
                UPDATE audit_runs AS run
                SET status = 'cancelled', version = version + 1
                WHERE run.tenant_id = %s AND run.case_id = %s
                  AND run.status IN ('queued', 'running', 'waiting')
                  AND EXISTS (
                    SELECT 1 FROM input_snapshot_documents AS source
                    WHERE source.tenant_id = run.tenant_id
                      AND source.snapshot_id = run.input_snapshot_id
                      AND source.document_version_id = %s
                  )
                RETURNING run.id
                """,
                (tenant_id, case_id, document_version_id),
            ).fetchall()
            invalidated_run_ids = [row["id"] for row in invalidated_rows]
            if invalidated_run_ids:
                connection.execute(
                    "UPDATE tasks SET status = 'cancelled', lease_owner = NULL, "
                    "lease_until = NULL, fencing_token = fencing_token + 1, updated_at = now() "
                    "WHERE tenant_id = %s AND run_id = ANY(%s::uuid[]) "
                    "AND status IN ('queued', 'running', 'waiting_human')",
                    (tenant_id, invalidated_run_ids),
                )
                connection.execute(
                    "UPDATE human_tasks SET status = 'cancelled', version = version + 1 "
                    "WHERE tenant_id = %s AND run_id = ANY(%s::uuid[]) AND status = 'open'",
                    (tenant_id, invalidated_run_ids),
                )

            for job_table in ("document_parse_jobs", "document_index_jobs"):
                connection.execute(
                    f"UPDATE {job_table} SET status = 'failed', lease_owner = NULL, "
                    "lease_until = NULL, fencing_token = fencing_token + 1, "
                    "last_error = 'document_removed', updated_at = now() "
                    "WHERE tenant_id = %s AND case_id = %s AND document_version_id = %s "
                    "AND status IN ('queued', 'running')",
                    (tenant_id, case_id, document_version_id),
                )

            connection.execute(
                "UPDATE document_versions SET status = 'revoked', removed_at = %s, "
                "removed_by = %s, parse_status = CASE WHEN parse_status IN ('queued', 'processing') "
                "THEN 'failed' ELSE parse_status END "
                "WHERE tenant_id = %s AND id = %s",
                (removed_at, actor_id, tenant_id, document_version_id),
            )

            next_version = int(case["version"]) + 1
            updated_case = connection.execute(
                "UPDATE audit_cases SET version = %s WHERE tenant_id = %s AND id = %s "
                "AND version = %s RETURNING version",
                (next_version, tenant_id, case_id, case["version"]),
            ).fetchone()
            if updated_case is None:
                raise DocumentRemovalConflict("case_version_changed")

            connection.execute(
                "INSERT INTO document_removal_events "
                "(tenant_id, case_id, document_version_id, actor_id, reason, idempotency_key, "
                "expected_case_version, case_version, revoked_selection_ids, revoked_grant_ids, "
                "invalidated_run_ids, permissions, removed_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                (
                    tenant_id,
                    case_id,
                    document_version_id,
                    actor_id,
                    normalized_reason,
                    idempotency_key,
                    expected_case_version,
                    next_version,
                    selection_ids,
                    grant_ids,
                    invalidated_run_ids,
                    json.dumps(permission_snapshot),
                    removed_at,
                ),
            )

            for selection_id in selection_ids:
                access_key = "remove-" + hashlib.sha256(
                    f"{idempotency_key}:{selection_id}".encode()
                ).hexdigest()
                connection.execute(
                    "INSERT INTO document_access_events "
                    "(tenant_id, case_id, document_version_id, selection_id, actor_id, action, "
                    "permissions, reason, idempotency_key, case_version, invalidated_run_ids, "
                    "effective_at) "
                    "VALUES (%s, %s, %s, %s, %s, 'selection_revoked', %s::jsonb, %s, %s, %s, %s, %s)",
                    (
                        tenant_id,
                        case_id,
                        document_version_id,
                        selection_id,
                        actor_id,
                        json.dumps(permission_snapshot),
                        normalized_reason,
                        access_key,
                        next_version,
                        invalidated_run_ids,
                        removed_at,
                    ),
                )

            return RemovedDocument(
                document_version_id=document_version_id,
                case_id=case_id,
                case_version=next_version,
                removed_at=removed_at,
                revoked_selection_count=len(selection_ids),
                revoked_grant_count=len(grant_ids),
                invalidated_run_count=len(invalidated_run_ids),
            )
