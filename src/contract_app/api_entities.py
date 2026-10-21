"""Tenant-scoped legal entity registry for case preparation and correction."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator

from contract_app.api_errors import COMMON_PROBLEM_RESPONSES
from contract_app.api_cases import get_database_url
from contract_app.api_snapshots import Principal, require_principal
from contract_app.auth_security import require_any_permission, require_permission

router = APIRouter(prefix="/api/v1/entities", tags=["Legal entities"], responses=COMMON_PROBLEM_RESPONSES)


class LegalEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1, max_length=255)
    unified_social_credit_code: str | None = Field(default=None, max_length=32)
    verification_basis: str = Field(min_length=1, max_length=2000)
    is_active: bool = True

    @field_validator("legal_name", "verification_basis")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text_required")
        return normalized

    @field_validator("unified_social_credit_code")
    @classmethod
    def normalize_credit_code(cls, value: str | None) -> str | None:
        normalized = value.strip().upper() if value else None
        return normalized or None


class LegalEntityResponse(BaseModel):
    legal_entity_id: UUID
    legal_name: str
    unified_social_credit_code: str | None
    verification_basis: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LegalEntityListResponse(BaseModel):
    items: list[LegalEntityResponse]
    total: int
    page: int
    page_size: int


@router.get(
    "",
    response_model=LegalEntityListResponse,
    dependencies=[Depends(require_any_permission("entity_registry.use", "entity_registry.manage"))],
)
def list_legal_entities(
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=120)] = None,
    include_inactive: bool = False,
) -> LegalEntityListResponse:
    if include_inactive and "entity_registry.manage" not in principal.permissions:
        raise HTTPException(status_code=403, detail="permission_required")

    normalized_query = query.strip() if query and query.strip() else None
    text_clause = ""
    if normalized_query:
        text_clause = (
            " AND (strpos(lower(legal_name), lower(%s)) > 0 "
            "OR strpos(lower(coalesce(unified_social_credit_code, '')), lower(%s)) > 0 "
            "OR strpos(lower(id::text), lower(%s)) > 0)"
        )
    query_values = [normalized_query, normalized_query, normalized_query] if normalized_query else []
    status_clause = "" if include_inactive else " AND is_active"

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(principal.tenant_id),)
        )
        total_row = connection.execute(
            f"SELECT count(*) AS total FROM legal_entities "
            f"WHERE tenant_id = %s{status_clause}{text_clause}",
            [principal.tenant_id, *query_values],
        ).fetchone()
        assert total_row is not None
        total = int(total_row["total"])
        last_page = max(1, (total + page_size - 1) // page_size)
        effective_page = min(page, last_page)
        rows = connection.execute(
            f"""
            SELECT id AS legal_entity_id, legal_name, unified_social_credit_code,
                   verification_basis, is_active, created_at, updated_at
            FROM legal_entities
            WHERE tenant_id = %s{status_clause}{text_clause}
            ORDER BY is_active DESC, lower(legal_name), id
            LIMIT %s OFFSET %s
            """,
            [principal.tenant_id, *query_values, page_size, (effective_page - 1) * page_size],
        ).fetchall()

    return LegalEntityListResponse(
        items=[LegalEntityResponse(**row) for row in rows],
        total=total,
        page=effective_page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=LegalEntityResponse,
    status_code=201,
    dependencies=[Depends(require_permission("entity_registry.manage"))],
)
def create_legal_entity(
    body: LegalEntityRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> LegalEntityResponse:
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)", (str(principal.tenant_id),)
            )
            row = connection.execute(
                """
                INSERT INTO legal_entities
                  (tenant_id, legal_name, unified_social_credit_code, verification_basis,
                   is_active, created_by, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id AS legal_entity_id, legal_name, unified_social_credit_code,
                          verification_basis, is_active, created_at, updated_at
                """,
                (
                    principal.tenant_id,
                    body.legal_name,
                    body.unified_social_credit_code,
                    body.verification_basis,
                    body.is_active,
                    principal.actor_id,
                    principal.actor_id,
                ),
            ).fetchone()
            assert row is not None
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="entity_credit_code_exists") from exc
    return LegalEntityResponse(**row)


@router.patch(
    "/{legal_entity_id}",
    response_model=LegalEntityResponse,
    dependencies=[Depends(require_permission("entity_registry.manage"))],
)
def update_legal_entity(
    legal_entity_id: UUID,
    body: LegalEntityRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> LegalEntityResponse:
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)", (str(principal.tenant_id),)
            )
            row = connection.execute(
                """
                UPDATE legal_entities
                SET legal_name = %s, unified_social_credit_code = %s,
                    verification_basis = %s, is_active = %s,
                    updated_by = %s, updated_at = clock_timestamp()
                WHERE tenant_id = %s AND id = %s
                RETURNING id AS legal_entity_id, legal_name, unified_social_credit_code,
                          verification_basis, is_active, created_at, updated_at
                """,
                (
                    body.legal_name,
                    body.unified_social_credit_code,
                    body.verification_basis,
                    body.is_active,
                    principal.actor_id,
                    principal.tenant_id,
                    legal_entity_id,
                ),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="entity_credit_code_exists") from exc
    return LegalEntityResponse(**row)
