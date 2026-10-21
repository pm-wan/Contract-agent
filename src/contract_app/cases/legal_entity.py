"""Reviewer-confirmed legal-entity scope for a case."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class CaseEntityDenied(PermissionError):
    """Actor is not a current reviewer of the case."""


class CaseEntityConflict(ValueError):
    """Case version or entity-confirmation state changed."""


class CaseEntityInvalid(ValueError):
    """Confirmation command is malformed."""


@dataclass(frozen=True, slots=True)
class CaseEntityConfirmation:
    case_id: UUID
    title: str
    legal_entity_id: UUID
    status: str
    version: int
    created_at: datetime
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class CaseEntityCorrection:
    case_id: UUID
    title: str
    legal_entity_id: UUID
    status: str
    version: int
    created_at: datetime
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class CaseEntityHistoryEvent:
    event_id: UUID
    event_type: str
    actor_id: UUID
    previous_legal_entity_id: UUID | None
    legal_entity_id: UUID
    reason: str
    case_version: int
    occurred_at: datetime
    revoked_selection_count: int
    revoked_grant_count: int
    superseded_run_count: int


@dataclass(frozen=True, slots=True)
class CaseEntityHistoryPage:
    items: tuple[CaseEntityHistoryEvent, ...]
    next_cursor: str | None


class CaseLegalEntityStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def confirm(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        legal_entity_id: UUID,
        expected_case_version: int,
        verification_note: str,
        idempotency_key: str,
    ) -> CaseEntityConfirmation:
        note = verification_note.strip()
        if (
            expected_case_version <= 0
            or not note
            or len(note) > 500
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None
        ):
            raise CaseEntityInvalid("case_entity_confirmation_invalid")
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "expected_case_version": expected_case_version,
                    "legal_entity_id": str(legal_entity_id),
                    "verification_note": note,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            case = connection.execute(
                "SELECT id, title, legal_entity_id, status, version, created_at "
                "FROM audit_cases WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, case_id),
            ).fetchone()
            member = connection.execute(
                "SELECT role FROM case_members WHERE tenant_id = %s AND case_id = %s "
                "AND actor_id = %s AND revoked_at IS NULL",
                (tenant_id, case_id, actor_id),
            ).fetchone()
            if case is None or member is None or member["role"] != "reviewer":
                raise CaseEntityDenied("reviewer_membership_required")
            prior = connection.execute(
                "SELECT actor_id, legal_entity_id, request_sha256, case_version, confirmed_at "
                "FROM case_legal_entity_confirmations WHERE tenant_id = %s AND case_id = %s "
                "AND idempotency_key = %s",
                (tenant_id, case_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior["actor_id"] != actor_id or prior["request_sha256"] != request_hash:
                    raise CaseEntityConflict("idempotency_key_conflict")
                return CaseEntityConfirmation(
                    case_id,
                    case["title"],
                    prior["legal_entity_id"],
                    case["status"],
                    int(prior["case_version"]),
                    case["created_at"],
                    prior["confirmed_at"],
                )
            if case["legal_entity_id"] is not None:
                raise CaseEntityConflict("case_entity_already_confirmed")
            if case["status"] not in {"draft", "ready"}:
                raise CaseEntityConflict("case_not_confirmable")
            if int(case["version"]) != expected_case_version:
                raise CaseEntityConflict("case_version_changed")
            primary_contract = connection.execute(
                "SELECT 1 FROM document_versions WHERE tenant_id = %s "
                "AND uploaded_case_id = %s AND source_kind = 'main' "
                "AND status IN ('staged', 'active') LIMIT 1",
                (tenant_id, case_id),
            ).fetchone()
            if primary_contract is None:
                raise CaseEntityConflict("primary_contract_required_before_entity_confirmation")

            next_version = expected_case_version + 1
            updated = connection.execute(
                "UPDATE audit_cases SET legal_entity_id = %s, version = %s "
                "WHERE tenant_id = %s AND id = %s AND legal_entity_id IS NULL "
                "AND version = %s RETURNING version",
                (legal_entity_id, next_version, tenant_id, case_id, expected_case_version),
            ).fetchone()
            if updated is None:
                raise CaseEntityConflict("case_version_changed")
            connection.execute(
                "UPDATE document_versions SET legal_entity_id = %s "
                "WHERE tenant_id = %s AND uploaded_case_id = %s "
                "AND legal_entity_id IS NULL AND status IN ('staged', 'active')",
                (legal_entity_id, tenant_id, case_id),
            )
            confirmation = connection.execute(
                "INSERT INTO case_legal_entity_confirmations "
                "(tenant_id, case_id, actor_id, legal_entity_id, expected_case_version, "
                "case_version, verification_note, idempotency_key, request_sha256) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING confirmed_at",
                (
                    tenant_id,
                    case_id,
                    actor_id,
                    legal_entity_id,
                    expected_case_version,
                    next_version,
                    note,
                    idempotency_key,
                    request_hash,
                ),
            ).fetchone()
            assert confirmation is not None
        return CaseEntityConfirmation(
            case_id,
            case["title"],
            legal_entity_id,
            case["status"],
            next_version,
            case["created_at"],
            confirmation["confirmed_at"],
        )

    def correct(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        legal_entity_id: UUID,
        expected_case_version: int,
        correction_note: str,
        idempotency_key: str,
    ) -> CaseEntityCorrection:
        """Correct a confirmed entity only through the separately provisioned admin role."""
        note = correction_note.strip()
        if (
            expected_case_version <= 0
            or not note
            or len(note) > 500
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None
        ):
            raise CaseEntityInvalid("case_entity_correction_invalid")
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "expected_case_version": expected_case_version,
                    "legal_entity_id": str(legal_entity_id),
                    "correction_note": note,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            case = connection.execute(
                "SELECT id, title, legal_entity_id, status, version, created_at "
                "FROM audit_cases WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, case_id),
            ).fetchone()
            administrator = connection.execute(
                "SELECT 1 FROM app_user_roles AS assignment "
                "JOIN app_roles AS role ON role.tenant_id = assignment.tenant_id "
                "AND role.id = assignment.role_id "
                "JOIN app_role_permissions AS permission ON permission.tenant_id = role.tenant_id "
                "AND permission.role_id = role.id "
                "WHERE assignment.tenant_id = %s AND assignment.actor_id = %s "
                "AND assignment.revoked_at IS NULL AND role.is_active "
                "AND permission.permission_key = 'entity_corrections.use'",
                (tenant_id, actor_id),
            ).fetchone()
            if case is None or administrator is None:
                raise CaseEntityDenied("entity_administrator_required")
            confirmation = connection.execute(
                "SELECT confirmed_at FROM case_legal_entity_confirmations "
                "WHERE tenant_id = %s AND case_id = %s",
                (tenant_id, case_id),
            ).fetchone()
            if confirmation is None:
                if case["legal_entity_id"] is None:
                    raise CaseEntityConflict("case_entity_not_confirmed")
                raise CaseEntityConflict("case_entity_confirmation_missing")
            prior = connection.execute(
                "SELECT actor_id, legal_entity_id, request_sha256, case_version "
                "FROM case_legal_entity_corrections WHERE tenant_id = %s AND case_id = %s "
                "AND idempotency_key = %s",
                (tenant_id, case_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior["actor_id"] != actor_id or prior["request_sha256"] != request_hash:
                    raise CaseEntityConflict("idempotency_key_conflict")
                return CaseEntityCorrection(
                    case_id,
                    case["title"],
                    prior["legal_entity_id"],
                    case["status"],
                    int(prior["case_version"]),
                    case["created_at"],
                    confirmation["confirmed_at"],
                )
            if case["legal_entity_id"] is None:
                raise CaseEntityConflict("case_entity_not_confirmed")
            if case["legal_entity_id"] == legal_entity_id:
                raise CaseEntityInvalid("case_entity_correction_noop")
            if case["status"] not in {"draft", "ready"}:
                raise CaseEntityConflict("case_not_correctable")
            if int(case["version"]) != expected_case_version:
                raise CaseEntityConflict("case_version_changed")
            next_version = expected_case_version + 1
            updated = connection.execute(
                "UPDATE audit_cases SET legal_entity_id = %s, version = %s "
                "WHERE tenant_id = %s AND id = %s AND legal_entity_id = %s AND version = %s "
                "RETURNING version",
                (
                    legal_entity_id,
                    next_version,
                    tenant_id,
                    case_id,
                    case["legal_entity_id"],
                    expected_case_version,
                ),
            ).fetchone()
            if updated is None:
                raise CaseEntityConflict("case_version_changed")

            # A legal-entity correction changes the case scope. Keep immutable
            # snapshots/results as historical records, but revoke the current
            # material selections and grants and retire every run bound to the
            # old scope so no worker can continue under the corrected case.
            selections = connection.execute(
                "UPDATE contract_selections SET revoked_at = clock_timestamp() "
                "WHERE tenant_id = %s AND case_id = %s AND revoked_at IS NULL "
                "RETURNING id, document_version_id",
                (tenant_id, case_id),
            ).fetchall()
            revoked_selection_ids = [row["id"] for row in selections]
            selected_document_ids = {row["document_version_id"] for row in selections}
            revoked_grant_ids: list[UUID] = []
            revoked_document_ids: set[UUID] = set()
            if selected_document_ids:
                revoked_grants = connection.execute(
                    "UPDATE document_grants SET revoked_at = clock_timestamp() "
                    "WHERE tenant_id = %s AND document_version_id = ANY(%s) "
                    "AND revoked_at IS NULL RETURNING id, document_version_id",
                    (tenant_id, list(selected_document_ids)),
                ).fetchall()
                revoked_grant_ids = [row["id"] for row in revoked_grants]
                revoked_document_ids = {row["document_version_id"] for row in revoked_grants}
            invalidated_runs = connection.execute(
                "UPDATE audit_runs SET status = 'superseded', version = version + 1 "
                "WHERE tenant_id = %s AND case_id = %s "
                "AND status IN ('queued', 'running', 'waiting', 'completed') "
                "RETURNING id",
                (tenant_id, case_id),
            ).fetchall()
            invalidated_run_ids = [row["id"] for row in invalidated_runs]
            if invalidated_run_ids:
                connection.execute(
                    "UPDATE tasks SET status = 'superseded', lease_owner = NULL, "
                    "lease_until = NULL, fencing_token = fencing_token + 1, updated_at = now() "
                    "WHERE tenant_id = %s AND run_id = ANY(%s) "
                    "AND status IN ('queued', 'running', 'waiting_human')",
                    (tenant_id, invalidated_run_ids),
                )
                connection.execute(
                    "UPDATE human_tasks SET status = 'superseded', version = version + 1 "
                    "WHERE tenant_id = %s AND run_id = ANY(%s) AND status = 'open'",
                    (tenant_id, invalidated_run_ids),
                )
            correction = connection.execute(
                "INSERT INTO case_legal_entity_corrections "
                "(tenant_id, case_id, actor_id, previous_legal_entity_id, legal_entity_id, "
                "expected_case_version, case_version, correction_note, idempotency_key, "
                "request_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    tenant_id,
                    case_id,
                    actor_id,
                    case["legal_entity_id"],
                    legal_entity_id,
                    expected_case_version,
                    next_version,
                    note,
                    idempotency_key,
                    request_hash,
                ),
            ).fetchone()
            assert correction is not None
            connection.execute(
                "INSERT INTO case_legal_entity_correction_effects "
                "(tenant_id, correction_id, case_id, revoked_selection_ids, "
                "revoked_grant_ids, revoked_document_version_ids, superseded_run_ids) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    tenant_id,
                    correction["id"],
                    case_id,
                    revoked_selection_ids,
                    revoked_grant_ids,
                    list(revoked_document_ids),
                    invalidated_run_ids,
                ),
            )
            return CaseEntityCorrection(
                case_id,
                case["title"],
                legal_entity_id,
                case["status"],
                next_version,
                case["created_at"],
                confirmation["confirmed_at"],
            )

    def list_history(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> CaseEntityHistoryPage:
        if not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            case = connection.execute(
                "SELECT 1 FROM audit_cases WHERE tenant_id = %s AND id = %s",
                (tenant_id, case_id),
            ).fetchone()
            if case is None:
                raise CaseEntityDenied("case_not_found")
            reviewer = connection.execute(
                "SELECT 1 FROM case_members WHERE tenant_id = %s AND case_id = %s "
                "AND actor_id = %s AND role = 'reviewer' AND revoked_at IS NULL",
                (tenant_id, case_id, actor_id),
            ).fetchone()
            administrator = connection.execute(
                "SELECT 1 FROM app_user_roles AS assignment "
                "JOIN app_roles AS role ON role.tenant_id = assignment.tenant_id "
                "AND role.id = assignment.role_id "
                "JOIN app_role_permissions AS permission ON permission.tenant_id = role.tenant_id "
                "AND permission.role_id = role.id "
                "WHERE assignment.tenant_id = %s AND assignment.actor_id = %s "
                "AND assignment.revoked_at IS NULL AND role.is_active "
                "AND permission.permission_key = 'entity_corrections.use'",
                (tenant_id, actor_id),
            ).fetchone()
            if reviewer is None and administrator is None:
                raise CaseEntityDenied("case_entity_history_forbidden")
            cursor_value: tuple[datetime, str, UUID] | None = None
            if cursor:
                try:
                    decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
                    occurred_at_text, event_type, event_id_text = decoded.split("|", 2)
                    if event_type not in {"confirmed", "corrected"}:
                        raise ValueError
                    cursor_value = (
                        datetime.fromisoformat(occurred_at_text),
                        event_type,
                        UUID(event_id_text),
                    )
                    if cursor_value[0].tzinfo is None:
                        raise ValueError
                except (ValueError, UnicodeDecodeError):
                    raise ValueError("invalid_cursor") from None
            rows = connection.execute(
                "SELECT * FROM ("
                "SELECT id AS event_id, 'confirmed'::text AS event_type, actor_id, "
                "NULL::uuid AS previous_legal_entity_id, legal_entity_id, "
                "verification_note AS reason, case_version, confirmed_at AS occurred_at, "
                "0::int AS revoked_selection_count, 0::int AS revoked_grant_count, "
                "0::int AS superseded_run_count "
                "FROM case_legal_entity_confirmations WHERE tenant_id = %s AND case_id = %s "
                "UNION ALL "
                "SELECT correction.id AS event_id, 'corrected'::text AS event_type, "
                "correction.actor_id, correction.previous_legal_entity_id, "
                "correction.legal_entity_id, correction.correction_note AS reason, "
                "correction.case_version, correction.corrected_at AS occurred_at, "
                "COALESCE(cardinality(effect.revoked_selection_ids), 0)::int "
                "AS revoked_selection_count, "
                "COALESCE(cardinality(effect.revoked_grant_ids), 0)::int "
                "AS revoked_grant_count, "
                "COALESCE(cardinality(effect.superseded_run_ids), 0)::int "
                "AS superseded_run_count "
                "FROM case_legal_entity_corrections AS correction "
                "LEFT JOIN case_legal_entity_correction_effects AS effect "
                "ON effect.tenant_id = correction.tenant_id "
                "AND effect.correction_id = correction.id "
                "WHERE correction.tenant_id = %s AND correction.case_id = %s"
                ") AS history_events "
                "WHERE (%s::timestamptz IS NULL OR "
                "(occurred_at, event_type, event_id) > (%s, %s, %s::uuid)) "
                "ORDER BY occurred_at, event_type, event_id LIMIT %s",
                (
                    tenant_id,
                    case_id,
                    tenant_id,
                    case_id,
                    cursor_value[0] if cursor_value else None,
                    cursor_value[0] if cursor_value else None,
                    cursor_value[1] if cursor_value else None,
                    cursor_value[2] if cursor_value else None,
                    limit + 1,
                ),
            ).fetchall()
        has_more = len(rows) > limit
        page = tuple(CaseEntityHistoryEvent(**row) for row in rows[:limit])
        next_cursor = None
        if has_more and page:
            last = page[-1]
            raw_cursor = (
                f"{last.occurred_at.isoformat()}|{last.event_type}|{last.event_id}"
            ).encode()
            next_cursor = base64.urlsafe_b64encode(raw_cursor).decode().rstrip("=")
        return CaseEntityHistoryPage(page, next_cursor)
