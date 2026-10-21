"""Explicit reviewer command for selecting and authorizing uploaded materials."""

import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

SelectionRelation = Literal["main", "attachment", "upstream"]
SelectionPurpose = Literal["contract_review", "upstream_comparison"]
MAX_GRANT_LIFETIME = timedelta(days=1)


class DocumentSelectionDenied(PermissionError):
    """Actor is not permitted to select material for the case."""


class DocumentSelectionConflict(ValueError):
    """Selection conflicts with current case or a previous authorization."""


class DocumentSelectionInvalid(ValueError):
    """Selection input is invalid."""


@dataclass(frozen=True, slots=True)
class SelectedDocument:
    selection_id: UUID
    grant_id: UUID
    document_version_id: UUID
    case_id: UUID
    relation: SelectionRelation
    purpose: SelectionPurpose
    allow_amount: bool
    valid_until: datetime
    case_version: int


@dataclass(frozen=True, slots=True)
class RevokedDocumentSelection:
    selection_id: UUID
    document_version_id: UUID
    case_id: UUID
    case_version: int
    revoked_at: datetime
    revoked_grant_count: int
    invalidated_run_count: int


@dataclass(frozen=True, slots=True)
class DocumentAccessEvent:
    id: UUID
    case_id: UUID
    document_version_id: UUID
    file_name: str
    selection_id: UUID
    actor_id: UUID
    action: Literal["selection_granted", "selection_revoked"]
    permissions: list[dict[str, object]]
    reason: str | None
    case_version: int
    invalidated_run_ids: list[UUID]
    created_at: datetime


class DocumentAccessAuditDenied(PermissionError):
    """Actor is not an active reviewer permitted to inspect access history."""


class DocumentAuthorizationStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def list_access_events(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[tuple[DocumentAccessEvent, ...], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        cursor_value: tuple[datetime, UUID] | None = None
        if cursor:
            try:
                decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
                created, event_id = decoded.split("|", 1)
                cursor_value = (datetime.fromisoformat(created), UUID(event_id))
            except (ValueError, UnicodeDecodeError):
                raise ValueError("invalid_cursor") from None
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            member = connection.execute(
                "SELECT role FROM case_members WHERE tenant_id = %s AND case_id = %s "
                "AND actor_id = %s AND revoked_at IS NULL",
                (tenant_id, case_id, actor_id),
            ).fetchone()
            if member is None or member["role"] != "reviewer":
                raise DocumentAccessAuditDenied("reviewer_membership_required")
            rows = connection.execute(
                """
                SELECT event.id, event.case_id, event.document_version_id,
                       document.source_name AS file_name, event.selection_id, event.actor_id,
                       event.action, event.permissions, event.reason, event.case_version,
                       event.invalidated_run_ids, event.created_at
                FROM document_access_events AS event
                JOIN document_versions AS document
                  ON document.tenant_id = event.tenant_id
                 AND document.id = event.document_version_id
                WHERE event.tenant_id = %s AND event.case_id = %s
                  AND (%s::timestamptz IS NULL OR (event.created_at, event.id) < (%s, %s::uuid))
                ORDER BY event.created_at DESC, event.id DESC LIMIT %s
                """,
                (
                    tenant_id,
                    case_id,
                    cursor_value[0] if cursor_value else None,
                    cursor_value[0] if cursor_value else None,
                    cursor_value[1] if cursor_value else None,
                    limit + 1,
                ),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            raw = f"{last['created_at'].isoformat()}|{last['id']}".encode()
            next_cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        return tuple(DocumentAccessEvent(**row) for row in page), next_cursor

    def select(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        document_version_id: UUID,
        relation: SelectionRelation,
        allow_amount: bool,
        valid_until: datetime,
        purpose: SelectionPurpose = "contract_review",
        confirm_project_relation: bool = False,
    ) -> SelectedDocument:
        now = datetime.now(UTC)
        if valid_until.tzinfo is None or not now < valid_until <= now + MAX_GRANT_LIFETIME:
            raise DocumentSelectionInvalid("grant_expiry_out_of_range")
        if (relation == "upstream" and purpose != "upstream_comparison") or (
            relation == "attachment" and purpose != "contract_review"
        ):
            raise DocumentSelectionInvalid("document_relation_purpose_mismatch")
        if (relation == "upstream") != confirm_project_relation:
            raise DocumentSelectionInvalid("project_relation_confirmation_required")

        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            case = connection.execute(
                "SELECT id, legal_entity_id, status, version FROM audit_cases "
                "WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, case_id),
            ).fetchone()
            member = connection.execute(
                "SELECT role FROM case_members WHERE tenant_id = %s AND case_id = %s "
                "AND actor_id = %s AND revoked_at IS NULL",
                (tenant_id, case_id, actor_id),
            ).fetchone()
            if case is None or member is None or member["role"] != "reviewer":
                raise DocumentSelectionDenied("reviewer_membership_required")
            if case["legal_entity_id"] is None:
                raise DocumentSelectionConflict("case_legal_entity_unconfirmed")
            if case["status"] not in {"draft", "ready"}:
                raise DocumentSelectionConflict("case_not_accepting_documents")

            document = connection.execute(
                "SELECT id, uploaded_case_id, legal_entity_id, source_kind, status "
                "FROM document_versions WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, document_version_id),
            ).fetchone()
            if document is None:
                raise DocumentSelectionDenied("document_not_available")
            if (
                document["uploaded_case_id"] != case_id
                or document["legal_entity_id"] != case["legal_entity_id"]
            ):
                raise DocumentSelectionDenied("document_not_available")
            expected_kind_by_relation = {
                "main": "main",
                "attachment": "attachment",
                "upstream": "customer",
            }
            expected_kind = expected_kind_by_relation[relation]
            if document["source_kind"] != expected_kind:
                raise DocumentSelectionConflict("document_role_relation_mismatch")
            if document["status"] not in {"staged", "active"}:
                raise DocumentSelectionConflict("document_not_selectable")

            selection = connection.execute(
                "SELECT id, relation FROM contract_selections WHERE tenant_id = %s "
                "AND case_id = %s AND document_version_id = %s AND relation = %s "
                "AND revoked_at IS NULL",
                (tenant_id, case_id, document_version_id, relation),
            ).fetchone()
            if selection is not None:
                existing_grant = connection.execute(
                    "SELECT id, allow_amount, valid_until FROM document_grants "
                    "WHERE tenant_id = %s AND actor_id = %s AND document_version_id = %s "
                    "AND purpose = %s AND revoked_at IS NULL",
                    (tenant_id, actor_id, document_version_id, purpose),
                ).fetchone()
                if existing_grant is not None and existing_grant["valid_until"] <= now:
                    # The partial unique index is over non-revoked grants, including
                    # expired rows. Retire an expired grant at its effective expiry
                    # so the same actor can explicitly authorize a fresh period.
                    connection.execute(
                        "UPDATE document_grants SET revoked_at = valid_until "
                        "WHERE tenant_id = %s AND id = %s AND revoked_at IS NULL",
                        (tenant_id, existing_grant["id"]),
                    )
                    existing_grant = None
                if existing_grant is not None:
                    if (
                        existing_grant["allow_amount"] != allow_amount
                        or existing_grant["valid_until"] != valid_until
                    ):
                        raise DocumentSelectionConflict("selection_already_authorized_differently")
                    return SelectedDocument(
                        selection["id"],
                        existing_grant["id"],
                        document_version_id,
                        case_id,
                        cast(SelectionRelation, selection["relation"]),
                        purpose,
                        existing_grant["allow_amount"],
                        existing_grant["valid_until"],
                        int(case["version"]),
                    )
            elif relation == "main":
                other_main = connection.execute(
                    "SELECT 1 FROM contract_selections WHERE tenant_id = %s AND case_id = %s "
                    "AND relation = 'main' AND revoked_at IS NULL",
                    (tenant_id, case_id),
                ).fetchone()
                if other_main is not None:
                    raise DocumentSelectionConflict("main_contract_already_selected")

            if selection is None and document["status"] == "staged":
                connection.execute(
                    "UPDATE document_versions SET status = 'active' "
                    "WHERE tenant_id = %s AND id = %s",
                    (tenant_id, document_version_id),
                )
            if selection is None:
                selection = connection.execute(
                    "INSERT INTO contract_selections "
                    "(tenant_id, case_id, document_version_id, relation, relation_ref, "
                    "relation_verified_at, selected_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        tenant_id,
                        case_id,
                        document_version_id,
                        relation,
                        f"case:{case_id}" if relation == "upstream" else None,
                        now if relation == "upstream" else None,
                        actor_id,
                    ),
                ).fetchone()
            grant = connection.execute(
                "INSERT INTO document_grants "
                "(tenant_id, actor_id, document_version_id, purpose, allow_text, allow_amount, "
                "valid_until) "
                "VALUES (%s, %s, %s, %s, true, %s, %s) RETURNING id, valid_until",
                (tenant_id, actor_id, document_version_id, purpose, allow_amount, valid_until),
            ).fetchone()
            assert selection is not None and grant is not None
            next_version = int(case["version"]) + 1
            updated_case = connection.execute(
                "UPDATE audit_cases SET version = %s WHERE tenant_id = %s AND id = %s "
                "AND version = %s RETURNING version",
                (next_version, tenant_id, case_id, case["version"]),
            ).fetchone()
            if updated_case is None:
                raise DocumentSelectionConflict("case_version_changed")
            connection.execute(
                "INSERT INTO document_access_events "
                "(tenant_id, case_id, document_version_id, selection_id, actor_id, action, "
                "permissions, case_version) "
                "VALUES (%s, %s, %s, %s, %s, 'selection_granted', %s::jsonb, %s)",
                (
                    tenant_id,
                    case_id,
                    document_version_id,
                    selection["id"],
                    actor_id,
                    json.dumps(
                        [
                            {
                                "actor_id": str(actor_id),
                                "purpose": purpose,
                                "allow_text": True,
                                "allow_amount": allow_amount,
                                "valid_until": grant["valid_until"].isoformat(),
                            }
                        ]
                    ),
                    next_version,
                ),
            )
            return SelectedDocument(
                selection["id"],
                grant["id"],
                document_version_id,
                case_id,
                relation,
                purpose,
                allow_amount,
                grant["valid_until"],
                next_version,
            )

    def revoke(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        selection_id: UUID,
        expected_case_version: int,
        reason: str,
        idempotency_key: str,
    ) -> RevokedDocumentSelection:
        if (
            expected_case_version <= 0
            or not reason.strip()
            or len(reason.strip()) > 500
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None
        ):
            raise DocumentSelectionInvalid("revoke_request_invalid")

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
            if case is None or member is None or member["role"] != "reviewer":
                raise DocumentSelectionDenied("reviewer_membership_required")

            prior = connection.execute(
                "SELECT * FROM document_access_events WHERE tenant_id = %s "
                "AND idempotency_key = %s",
                (tenant_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if (
                    prior["case_id"] != case_id
                    or prior["selection_id"] != selection_id
                    or prior["actor_id"] != actor_id
                    or prior["action"] != "selection_revoked"
                ):
                    raise DocumentSelectionConflict("idempotency_key_conflict")
                permissions = prior["permissions"]
                return RevokedDocumentSelection(
                    selection_id,
                    prior["document_version_id"],
                    case_id,
                    prior["case_version"],
                    prior["effective_at"],
                    len(permissions),
                    len(prior["invalidated_run_ids"]),
                )

            if case["version"] != expected_case_version:
                raise DocumentSelectionConflict("case_version_changed")
            selection = connection.execute(
                "SELECT document_version_id, relation, revoked_at FROM contract_selections "
                "WHERE tenant_id = %s AND case_id = %s AND id = %s FOR UPDATE",
                (tenant_id, case_id, selection_id),
            ).fetchone()
            if selection is None:
                raise DocumentSelectionDenied("selection_not_available")
            if selection["revoked_at"] is not None:
                raise DocumentSelectionConflict("selection_already_revoked")

            grants = connection.execute(
                "SELECT actor_id, purpose, allow_text, allow_amount, valid_until "
                "FROM document_grants WHERE tenant_id = %s AND document_version_id = %s "
                "AND revoked_at IS NULL FOR UPDATE",
                (tenant_id, selection["document_version_id"]),
            ).fetchall()
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
            revoked_at_row = connection.execute(
                "UPDATE contract_selections SET revoked_at = clock_timestamp() "
                "WHERE tenant_id = %s AND case_id = %s AND id = %s AND revoked_at IS NULL "
                "RETURNING revoked_at",
                (tenant_id, case_id, selection_id),
            ).fetchone()
            if revoked_at_row is None:
                raise DocumentSelectionConflict("selection_already_revoked")
            connection.execute(
                "UPDATE document_grants SET revoked_at = %s WHERE tenant_id = %s "
                "AND document_version_id = %s AND revoked_at IS NULL",
                (revoked_at_row["revoked_at"], tenant_id, selection["document_version_id"]),
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
                      AND source.selection_id = %s
                  )
                RETURNING run.id
                """,
                (tenant_id, case_id, selection_id),
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
            next_version = int(case["version"]) + 1
            updated_case = connection.execute(
                "UPDATE audit_cases SET version = %s WHERE tenant_id = %s AND id = %s "
                "AND version = %s RETURNING version",
                (next_version, tenant_id, case_id, case["version"]),
            ).fetchone()
            if updated_case is None:
                raise DocumentSelectionConflict("case_version_changed")
            connection.execute(
                "INSERT INTO document_access_events "
                "(tenant_id, case_id, document_version_id, selection_id, actor_id, action, "
                "permissions, reason, idempotency_key, case_version, invalidated_run_ids, "
                "effective_at) "
                "VALUES (%s, %s, %s, %s, %s, 'selection_revoked', %s::jsonb, %s, %s, %s, %s, %s)",
                (
                    tenant_id,
                    case_id,
                    selection["document_version_id"],
                    selection_id,
                    actor_id,
                    json.dumps(permission_snapshot),
                    reason.strip(),
                    idempotency_key,
                    next_version,
                    invalidated_run_ids,
                    revoked_at_row["revoked_at"],
                ),
            )
            return RevokedDocumentSelection(
                selection_id,
                selection["document_version_id"],
                case_id,
                next_version,
                revoked_at_row["revoked_at"],
                len(permission_snapshot),
                len(invalidated_run_ids),
            )
