"""Tenant-scoped case creation and member-visible reads."""

import hashlib
import os
import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from contract_app.api_errors import COMMON_PROBLEM_RESPONSES, problem_responses
from contract_app.api_snapshots import Principal, require_principal
from contract_app.auth_security import require_any_permission, require_permission
from contract_app.cases.legal_entity import (
    CaseEntityConflict,
    CaseEntityDenied,
    CaseEntityInvalid,
    CaseLegalEntityStore,
)

router = APIRouter(prefix="/api/v1/cases", tags=["Cases"], responses=COMMON_PROBLEM_RESPONSES)


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)


class ConfirmCaseLegalEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_entity_id: UUID
    expected_case_version: int = Field(ge=1)
    verification_note: str = Field(min_length=1, max_length=500)


class CorrectCaseLegalEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_entity_id: UUID
    expected_case_version: int = Field(ge=1)
    correction_note: str = Field(min_length=1, max_length=500)


class CaseResponse(BaseModel):
    case_id: UUID
    title: str
    legal_entity_id: UUID | None
    legal_entity_name: str | None = None
    legal_entity_confirmed_at: datetime | None
    status: str
    version: int
    created_at: datetime
    can_correct_legal_entity: bool = False


class CaseListResponse(BaseModel):
    items: list[CaseResponse]
    total: int
    page: int
    page_size: int


class EntityCorrectionQueueItem(BaseModel):
    case_id: UUID
    title: str
    legal_entity_id: UUID
    version: int


class EntityCorrectionQueueResponse(BaseModel):
    items: list[EntityCorrectionQueueItem]
    total: int
    page: int
    page_size: int


class EntityCorrectionContextResponse(BaseModel):
    case_id: UUID
    legal_entity_id: UUID
    version: int


class CaseLegalEntityHistoryEventResponse(BaseModel):
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


class CaseLegalEntityHistoryResponse(BaseModel):
    items: list[CaseLegalEntityHistoryEventResponse]
    next_cursor: str | None


def get_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return database_url


def _is_entity_administrator(database_url: str, tenant_id: UUID, actor_id: UUID) -> bool:
    """Return only the narrowly scoped capability used to render correction controls."""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        row = connection.execute(
            """
            SELECT EXISTS (
              SELECT 1
              FROM app_user_roles AS assignment
              JOIN app_roles AS role
                ON role.tenant_id = assignment.tenant_id AND role.id = assignment.role_id
              JOIN app_role_permissions AS permission
                ON permission.tenant_id = role.tenant_id AND permission.role_id = role.id
              WHERE assignment.tenant_id = %s AND assignment.actor_id = %s
                AND assignment.revoked_at IS NULL AND role.is_active
                AND permission.permission_key = 'entity_corrections.use'
            ) AS allowed
            """,
            (tenant_id, actor_id),
        ).fetchone()
    return bool(row and row["allowed"])


def get_case_legal_entity_store(
    database_url: Annotated[str, Depends(get_database_url)],
) -> CaseLegalEntityStore:
    return CaseLegalEntityStore(database_url)


def _registered_entity_name(
    database_url: str,
    tenant_id: UUID,
    legal_entity_id: UUID,
    *,
    active_only: bool,
) -> str | None:
    active_clause = " AND is_active" if active_only else ""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        row = connection.execute(
            "SELECT legal_name FROM legal_entities "
            f"WHERE tenant_id = %s AND id = %s{active_clause}",
            (tenant_id, legal_entity_id),
        ).fetchone()
    return str(row["legal_name"]) if row is not None else None


@router.get(
    "/legal-entity-correction-cases",
    operation_id="listEntityCorrectionCases",
    response_model=EntityCorrectionQueueResponse,
    responses=problem_responses(403),
    dependencies=[Depends(require_permission("entity_corrections.use"))],
)
def list_entity_correction_cases(
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=120)] = None,
) -> EntityCorrectionQueueResponse:
    """Show tenant cases in a correction-only projection to entity administrators."""
    normalized_query = query.strip() if query and query.strip() else None
    query_clause = " AND strpos(lower(c.title), lower(%s)) > 0" if normalized_query else ""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(principal.tenant_id),)
        )
        allowed = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM app_user_roles assignment "
            "JOIN app_roles role ON role.tenant_id = assignment.tenant_id "
            "AND role.id = assignment.role_id "
            "JOIN app_role_permissions permission ON permission.tenant_id = role.tenant_id "
            "AND permission.role_id = role.id "
            "WHERE assignment.tenant_id = %s AND assignment.actor_id = %s "
            "AND assignment.revoked_at IS NULL AND role.is_active "
            "AND permission.permission_key = 'entity_corrections.use') AS allowed",
            (principal.tenant_id, principal.actor_id),
        ).fetchone()
        if not allowed or not allowed["allowed"]:
            raise HTTPException(status_code=403, detail="entity_administrator_required")
        count = connection.execute(
            "SELECT count(*) AS total FROM audit_cases c "
            "JOIN case_legal_entity_confirmations confirmation "
            "ON confirmation.tenant_id = c.tenant_id AND confirmation.case_id = c.id "
            "WHERE c.tenant_id = %s" + query_clause,
            [principal.tenant_id] + ([normalized_query] if normalized_query else []),
        ).fetchone()
        total = int(count["total"]) if count else 0
        effective_page = min(page, max(1, (total + page_size - 1) // page_size))
        rows = connection.execute(
            "SELECT c.id AS case_id, c.title, c.legal_entity_id, c.version "
            "FROM audit_cases c JOIN case_legal_entity_confirmations confirmation "
            "ON confirmation.tenant_id = c.tenant_id AND confirmation.case_id = c.id "
            "WHERE c.tenant_id = %s"
            + query_clause
            + " ORDER BY c.created_at DESC, c.id LIMIT %s OFFSET %s",
            [principal.tenant_id]
            + ([normalized_query] if normalized_query else [])
            + [page_size, (effective_page - 1) * page_size],
        ).fetchall()
    return EntityCorrectionQueueResponse(
        items=[EntityCorrectionQueueItem(**row) for row in rows],
        total=total,
        page=effective_page,
        page_size=page_size,
    )


@router.get(
    "/{case_id}/legal-entity-correction-context",
    operation_id="getEntityCorrectionContext",
    response_model=EntityCorrectionContextResponse,
    responses=problem_responses(404),
    dependencies=[Depends(require_permission("entity_corrections.use"))],
)
def get_entity_correction_context(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> EntityCorrectionContextResponse:
    """Return only identifiers needed by the isolated administrator correction flow."""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(principal.tenant_id),)
        )
        row = connection.execute(
            "SELECT c.id AS case_id, c.legal_entity_id, c.version "
            "FROM audit_cases c JOIN case_legal_entity_confirmations confirmation "
            "ON confirmation.tenant_id = c.tenant_id AND confirmation.case_id = c.id "
            "WHERE c.tenant_id = %s AND c.id = %s AND EXISTS ("
            "SELECT 1 FROM app_user_roles assignment "
            "JOIN app_roles role ON role.tenant_id = assignment.tenant_id "
            "AND role.id = assignment.role_id "
            "JOIN app_role_permissions permission ON permission.tenant_id = role.tenant_id "
            "AND permission.role_id = role.id "
            "WHERE assignment.tenant_id = c.tenant_id AND assignment.actor_id = %s "
            "AND assignment.revoked_at IS NULL AND role.is_active "
            "AND permission.permission_key = 'entity_corrections.use')",
            (principal.tenant_id, case_id, principal.actor_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return EntityCorrectionContextResponse(**row)


@router.post(
    "",
    operation_id="createCase",
    response_model=CaseResponse,
    status_code=201,
    responses=problem_responses(409, 422),
    dependencies=[Depends(require_permission("cases.use"))],
)
def create_case(
    body: CreateCaseRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> CaseResponse:
    title = body.title.strip()
    if not title or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None:
        raise HTTPException(status_code=422, detail="case_creation_invalid")
    request_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(principal.tenant_id),)
        )
        row = connection.execute(
            "INSERT INTO audit_cases (tenant_id, title, create_key, create_request_sha256) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, create_key) WHERE create_key IS NOT NULL DO NOTHING "
            "RETURNING id AS case_id, title, legal_entity_id, status, version, created_at",
            (principal.tenant_id, title, idempotency_key, request_hash),
        ).fetchone()
        if row is not None:
            connection.execute(
                "INSERT INTO case_members (tenant_id, case_id, actor_id, role) "
                "VALUES (%s, %s, %s, 'reviewer')",
                (principal.tenant_id, row["case_id"], principal.actor_id),
            )
            return CaseResponse(**row, legal_entity_confirmed_at=None)
        existing = connection.execute(
            "SELECT c.id AS case_id, c.title, c.legal_entity_id, c.status, c.version, "
            "c.created_at, "
            "c.create_request_sha256, m.actor_id "
            "FROM audit_cases c LEFT JOIN case_members m ON m.tenant_id = c.tenant_id "
            "AND m.case_id = c.id AND m.actor_id = %s AND m.revoked_at IS NULL "
            "WHERE c.tenant_id = %s AND c.create_key = %s",
            (principal.actor_id, principal.tenant_id, idempotency_key),
        ).fetchone()
        if (
            existing is None
            or existing["actor_id"] != principal.actor_id
            or existing["create_request_sha256"] != request_hash
        ):
            raise HTTPException(status_code=409, detail="idempotency_key_conflict")
        return CaseResponse(
            case_id=existing["case_id"],
            title=existing["title"],
            legal_entity_id=existing["legal_entity_id"],
            legal_entity_confirmed_at=None,
            status=existing["status"],
            version=existing["version"],
            created_at=existing["created_at"],
        )


@router.post(
    "/{case_id}/legal-entity-confirmations",
    operation_id="confirmCaseLegalEntity",
    response_model=CaseResponse,
    responses=problem_responses(404, 409),
    dependencies=[Depends(require_permission("cases.use"))],
)
def confirm_case_legal_entity(
    case_id: UUID,
    body: ConfirmCaseLegalEntityRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
    store: Annotated[CaseLegalEntityStore, Depends(get_case_legal_entity_store)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> CaseResponse:
    legal_entity_name = _registered_entity_name(
        database_url, principal.tenant_id, body.legal_entity_id, active_only=True
    )
    if legal_entity_name is None:
        raise HTTPException(status_code=422, detail="registered_legal_entity_required")
    try:
        confirmation = store.confirm(
            principal.tenant_id,
            case_id,
            principal.actor_id,
            body.legal_entity_id,
            body.expected_case_version,
            body.verification_note,
            idempotency_key,
        )
    except CaseEntityDenied as exc:
        raise HTTPException(status_code=404, detail="not_found") from exc
    except CaseEntityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CaseEntityInvalid as exc:
        raise HTTPException(status_code=422, detail="confirmation_invalid") from exc
    return CaseResponse(
        case_id=confirmation.case_id,
        title=confirmation.title,
        legal_entity_id=confirmation.legal_entity_id,
        legal_entity_name=legal_entity_name,
        legal_entity_confirmed_at=confirmation.confirmed_at,
        status=confirmation.status,
        version=confirmation.version,
        created_at=confirmation.created_at,
        can_correct_legal_entity=_is_entity_administrator(
            database_url, principal.tenant_id, principal.actor_id
        ),
    )


@router.post(
    "/{case_id}/legal-entity-corrections",
    operation_id="correctCaseLegalEntity",
    response_model=EntityCorrectionContextResponse,
    responses=problem_responses(404, 409, 422),
    dependencies=[Depends(require_permission("entity_corrections.use"))],
)
def correct_case_legal_entity(
    case_id: UUID,
    body: CorrectCaseLegalEntityRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
    store: Annotated[CaseLegalEntityStore, Depends(get_case_legal_entity_store)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> EntityCorrectionContextResponse:
    if _registered_entity_name(
        database_url, principal.tenant_id, body.legal_entity_id, active_only=True
    ) is None:
        raise HTTPException(status_code=422, detail="registered_legal_entity_required")
    try:
        correction = store.correct(
            principal.tenant_id,
            case_id,
            principal.actor_id,
            body.legal_entity_id,
            body.expected_case_version,
            body.correction_note,
            idempotency_key,
        )
    except CaseEntityDenied as exc:
        raise HTTPException(status_code=404, detail="not_found") from exc
    except CaseEntityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CaseEntityInvalid as exc:
        raise HTTPException(status_code=422, detail="correction_invalid") from exc
    return EntityCorrectionContextResponse(
        case_id=correction.case_id,
        legal_entity_id=correction.legal_entity_id,
        version=correction.version,
    )


@router.get(
    "/{case_id}/legal-entity-history",
    operation_id="listCaseLegalEntityHistory",
    response_model=CaseLegalEntityHistoryResponse,
    responses=problem_responses(404),
    dependencies=[Depends(require_any_permission("cases.use", "entity_corrections.use"))],
)
def list_case_legal_entity_history(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    store: Annotated[CaseLegalEntityStore, Depends(get_case_legal_entity_store)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CaseLegalEntityHistoryResponse:
    try:
        history = store.list_history(
            principal.tenant_id,
            case_id,
            principal.actor_id,
            cursor=cursor,
            limit=limit,
        )
    except CaseEntityDenied as exc:
        raise HTTPException(status_code=404, detail="not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_history_cursor_or_limit") from exc
    return CaseLegalEntityHistoryResponse(
        items=[
            CaseLegalEntityHistoryEventResponse.model_validate(event, from_attributes=True)
            for event in history.items
        ],
        next_cursor=history.next_cursor,
    )


@router.get(
    "",
    operation_id="listCases",
    response_model=CaseListResponse,
    dependencies=[Depends(require_permission("cases.use"))],
)
def list_cases(
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=120)] = None,
) -> CaseListResponse:
    normalized_query = query.strip() if query and query.strip() else None
    query_clause = " AND strpos(lower(c.title), lower(%s)) > 0" if normalized_query else ""
    parameters: list[object] = [principal.tenant_id, principal.actor_id]
    if normalized_query is not None:
        parameters.append(normalized_query)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(principal.tenant_id),)
        )
        total_row = connection.execute(
            f"""
            SELECT count(*) AS total
            FROM audit_cases AS c
            JOIN case_members AS m
              ON m.tenant_id = c.tenant_id AND m.case_id = c.id
            WHERE c.tenant_id = %s AND m.actor_id = %s AND m.revoked_at IS NULL
              {query_clause}
            """,
            parameters,
        ).fetchone()
        assert total_row is not None
        total = int(total_row["total"])
        last_page = max(1, (total + page_size - 1) // page_size)
        effective_page = min(page, last_page)
        rows = connection.execute(
            f"""
            SELECT c.id AS case_id, c.title, c.legal_entity_id,
                   entity_catalog.legal_name AS legal_entity_name,
                   entity_event.confirmed_at AS legal_entity_confirmed_at,
                   c.status, c.version, c.created_at,
                   EXISTS (
                     SELECT 1 FROM app_user_roles AS assignment
                     JOIN app_roles AS role
                       ON role.tenant_id = assignment.tenant_id AND role.id = assignment.role_id
                     JOIN app_role_permissions AS permission
                       ON permission.tenant_id = role.tenant_id AND permission.role_id = role.id
                     WHERE assignment.tenant_id = c.tenant_id
                       AND assignment.actor_id = %s AND assignment.revoked_at IS NULL
                       AND role.is_active AND permission.permission_key = 'entity_corrections.use'
                   ) AS can_correct_legal_entity
            FROM audit_cases AS c
            JOIN case_members AS m
              ON m.tenant_id = c.tenant_id AND m.case_id = c.id
            LEFT JOIN case_legal_entity_confirmations AS entity_event
              ON entity_event.tenant_id = c.tenant_id AND entity_event.case_id = c.id
            LEFT JOIN legal_entities AS entity_catalog
              ON entity_catalog.tenant_id = c.tenant_id AND entity_catalog.id = c.legal_entity_id
            WHERE c.tenant_id = %s AND m.actor_id = %s AND m.revoked_at IS NULL
              {query_clause}
            ORDER BY c.created_at DESC, c.id
            LIMIT %s OFFSET %s
            """,
            [principal.actor_id, principal.tenant_id, principal.actor_id]
            + ([normalized_query] if normalized_query is not None else [])
            + [page_size, (effective_page - 1) * page_size],
        ).fetchall()
    return CaseListResponse(
        items=[CaseResponse(**row) for row in rows],
        total=total,
        page=effective_page,
        page_size=page_size,
    )


@router.get(
    "/{case_id}",
    operation_id="getCase",
    response_model=CaseResponse,
    responses=problem_responses(404),
    dependencies=[Depends(require_permission("cases.use"))],
)
def get_case(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> CaseResponse:
    """Return only cases for which the current actor has active membership."""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(principal.tenant_id),)
        )
        row = connection.execute(
            """
            SELECT c.id AS case_id, c.title, c.legal_entity_id,
                   entity_catalog.legal_name AS legal_entity_name,
                   entity_event.confirmed_at AS legal_entity_confirmed_at,
                   c.status, c.version, c.created_at,
                   EXISTS (
                     SELECT 1 FROM app_user_roles AS assignment
                     JOIN app_roles AS role
                       ON role.tenant_id = assignment.tenant_id AND role.id = assignment.role_id
                     JOIN app_role_permissions AS permission
                       ON permission.tenant_id = role.tenant_id AND permission.role_id = role.id
                     WHERE assignment.tenant_id = c.tenant_id
                       AND assignment.actor_id = %s AND assignment.revoked_at IS NULL
                       AND role.is_active AND permission.permission_key = 'entity_corrections.use'
                   ) AS can_correct_legal_entity
            FROM audit_cases AS c
            JOIN case_members AS m
              ON m.tenant_id = c.tenant_id AND m.case_id = c.id
            LEFT JOIN case_legal_entity_confirmations AS entity_event
              ON entity_event.tenant_id = c.tenant_id AND entity_event.case_id = c.id
            LEFT JOIN legal_entities AS entity_catalog
              ON entity_catalog.tenant_id = c.tenant_id AND entity_catalog.id = c.legal_entity_id
            WHERE c.tenant_id = %s AND c.id = %s
              AND m.actor_id = %s AND m.revoked_at IS NULL
            """,
            (principal.actor_id, principal.tenant_id, case_id, principal.actor_id),
        ).fetchone()
    if row is None:
        # Avoid disclosing whether an inaccessible case exists.
        raise HTTPException(status_code=404, detail="not_found")
    return CaseResponse(**row)
