"""Organization, user, and role administration for workspace administrators."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator

from contract_app.api_errors import COMMON_PROBLEM_RESPONSES
from contract_app.auth_catalog import PERMISSIONS, SELECTABLE_PERMISSION_KEYS
from contract_app.auth_security import (
    Principal,
    database_url,
    execute_many,
    hash_password,
    require_permission,
    set_tenant,
)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["Access administration"],
    responses=COMMON_PROBLEM_RESPONSES,
    dependencies=[Depends(require_permission("admin.manage"))],
)


class OrganizationUnitView(BaseModel):
    id: UUID
    parent_id: UUID | None
    code: str
    name: str
    is_active: bool
    created_at: datetime


class OrganizationUnitListResponse(BaseModel):
    items: list[OrganizationUnitView]


class CreateOrganizationUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: UUID | None = None
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()


class UpdateOrganizationUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=64)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()


class RoleView(BaseModel):
    id: UUID
    role_key: str
    name: str
    description: str
    is_system: bool
    is_active: bool
    permissions: list[str]


class PermissionView(BaseModel):
    key: str
    name: str
    description: str


class RoleListResponse(BaseModel):
    roles: list[RoleView]
    permissions: list[PermissionView]


class RoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    permissions: list[str] = Field(min_length=1, max_length=10)

    @field_validator("name", "description")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, values: list[str]) -> list[str]:
        unique_values = sorted(set(values))
        if not unique_values or not set(unique_values).issubset(SELECTABLE_PERMISSION_KEYS):
            raise ValueError("role_permissions_invalid")
        return unique_values


class UserRoleView(BaseModel):
    id: UUID
    role_key: str
    name: str


class UserView(BaseModel):
    actor_id: UUID
    username: str
    display_name: str
    organization_id: UUID
    organization_name: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    roles: list[UserRoleView]


class UserListResponse(BaseModel):
    items: list[UserView]


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    organization_id: UUID
    role_ids: list[UUID] = Field(min_length=1, max_length=20)
    initial_password: str = Field(min_length=12, max_length=128)

    @field_validator("username", "display_name")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    organization_id: UUID
    role_ids: list[UUID] = Field(min_length=1, max_length=20)
    is_active: bool
    new_password: str | None = Field(default=None, min_length=12, max_length=128)

    @field_validator("display_name")
    @classmethod
    def trim_display_name(cls, value: str) -> str:
        return value.strip()


def _active_organization_exists(
    connection: psycopg.Connection,
    tenant_id: UUID,
    organization_id: UUID,
) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM organization_units "
            "WHERE tenant_id = %s AND id = %s AND is_active",
            (tenant_id, organization_id),
        ).fetchone()
        is not None
    )


def _validate_role_ids(
    connection: psycopg.Connection,
    tenant_id: UUID,
    role_ids: list[UUID],
) -> list[UUID]:
    unique_ids = list(dict.fromkeys(role_ids))
    rows = connection.execute(
        "SELECT id FROM app_roles WHERE tenant_id = %s AND id = ANY(%s) AND is_active",
        (tenant_id, unique_ids),
    ).fetchall()
    if len(rows) != len(unique_ids):
        raise HTTPException(status_code=422, detail="role_assignment_invalid")
    return unique_ids


def _user_view(
    connection: psycopg.Connection,
    tenant_id: UUID,
    actor_id: UUID,
) -> UserView:
    row = connection.execute(
        """
        SELECT account.actor_id, account.username, account.display_name,
               account.organization_id, organization.name AS organization_name,
               account.is_active, account.created_at, account.last_login_at,
               COALESCE((
                 SELECT jsonb_agg(jsonb_build_object(
                   'id', role.id, 'role_key', role.role_key, 'name', role.name
                 ) ORDER BY role.name)
                 FROM app_user_roles AS assignment
                 JOIN app_roles AS role
                   ON role.tenant_id = assignment.tenant_id AND role.id = assignment.role_id
                 WHERE assignment.tenant_id = account.tenant_id
                   AND assignment.actor_id = account.actor_id
                   AND assignment.revoked_at IS NULL AND role.is_active
               ), '[]'::jsonb) AS roles
        FROM app_users AS account
        JOIN organization_units AS organization
          ON organization.tenant_id = account.tenant_id
         AND organization.id = account.organization_id
        WHERE account.tenant_id = %s AND account.actor_id = %s
        """,
        (tenant_id, actor_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return UserView(**row)


def _has_active_system_administrator(
    connection: psycopg.Connection,
    tenant_id: UUID,
) -> bool:
    row = connection.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM app_users AS account
          JOIN app_user_roles AS assignment
            ON assignment.tenant_id = account.tenant_id
           AND assignment.actor_id = account.actor_id
          JOIN app_roles AS role
            ON role.tenant_id = assignment.tenant_id AND role.id = assignment.role_id
          WHERE account.tenant_id = %s AND account.is_active
            AND assignment.revoked_at IS NULL AND role.is_active
            AND role.role_key = 'workspace_admin'
        ) AS has_admin
        """,
        (tenant_id,),
    ).fetchone()
    return bool(row and row["has_admin"])


@router.get("/org-units", response_model=OrganizationUnitListResponse)
def list_organization_units(
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> OrganizationUnitListResponse:
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        rows = connection.execute(
            "SELECT id, parent_id, code, name, is_active, created_at "
            "FROM organization_units WHERE tenant_id = %s "
            "ORDER BY parent_id NULLS FIRST, name, id",
            (principal.tenant_id,),
        ).fetchall()
    return OrganizationUnitListResponse(items=[OrganizationUnitView(**row) for row in rows])


@router.post("/org-units", response_model=OrganizationUnitView, status_code=201)
def create_organization_unit(
    body: CreateOrganizationUnitRequest,
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> OrganizationUnitView:
    if not body.code or not body.name:
        raise HTTPException(status_code=422, detail="organization_invalid")
    try:
        with psycopg.connect(database_url(), row_factory=dict_row) as connection:
            set_tenant(connection, principal.tenant_id)
            if body.parent_id and not _active_organization_exists(
                connection, principal.tenant_id, body.parent_id
            ):
                raise HTTPException(status_code=422, detail="organization_parent_invalid")
            row = connection.execute(
                "INSERT INTO organization_units "
                "(tenant_id, parent_id, code, name, created_by, updated_by) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "RETURNING id, parent_id, code, name, is_active, created_at",
                (
                    principal.tenant_id,
                    body.parent_id,
                    body.code,
                    body.name,
                    principal.actor_id,
                    principal.actor_id,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="organization_code_exists") from exc
    assert row is not None
    return OrganizationUnitView(**row)


@router.patch("/org-units/{organization_id}", response_model=OrganizationUnitView)
def update_organization_unit(
    organization_id: UUID,
    body: UpdateOrganizationUnitRequest,
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> OrganizationUnitView:
    if not body.code or not body.name:
        raise HTTPException(status_code=422, detail="organization_invalid")
    try:
        with psycopg.connect(database_url(), row_factory=dict_row) as connection:
            set_tenant(connection, principal.tenant_id)
            if body.parent_id and not _active_organization_exists(
                connection, principal.tenant_id, body.parent_id
            ):
                raise HTTPException(status_code=422, detail="organization_parent_invalid")
            if body.parent_id:
                creates_cycle = connection.execute(
                    """
                    WITH RECURSIVE descendants(id) AS (
                      SELECT id FROM organization_units
                      WHERE tenant_id = %s AND id = %s
                      UNION ALL
                      SELECT child.id FROM organization_units AS child
                      JOIN descendants AS parent ON child.parent_id = parent.id
                      WHERE child.tenant_id = %s AND child.is_active
                    )
                    SELECT EXISTS (SELECT 1 FROM descendants WHERE id = %s) AS creates_cycle
                    """,
                    (principal.tenant_id, organization_id, principal.tenant_id, body.parent_id),
                ).fetchone()
                if creates_cycle and creates_cycle["creates_cycle"]:
                    raise HTTPException(status_code=422, detail="organization_parent_invalid")
            row = connection.execute(
                "UPDATE organization_units SET parent_id = %s, code = %s, name = %s, updated_by = %s, "
                "updated_at = clock_timestamp() WHERE tenant_id = %s AND id = %s "
                "RETURNING id, parent_id, code, name, is_active, created_at",
                (
                    body.parent_id,
                    body.code,
                    body.name,
                    principal.actor_id,
                    principal.tenant_id,
                    organization_id,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="organization_code_exists") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return OrganizationUnitView(**row)


@router.delete("/org-units/{organization_id}", status_code=204)
def delete_organization_unit(
    organization_id: UUID,
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> Response:
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        counts = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM organization_units child "
            " WHERE child.tenant_id = %s AND child.parent_id = %s) AS children, "
            "(SELECT count(*) FROM app_users account "
            " WHERE account.tenant_id = %s AND account.organization_id = %s) AS users",
            (principal.tenant_id, organization_id, principal.tenant_id, organization_id),
        ).fetchone()
        if counts is None:
            raise HTTPException(status_code=404, detail="not_found")
        if counts["children"] or counts["users"]:
            raise HTTPException(status_code=409, detail="organization_not_empty")
        deleted = connection.execute(
            "DELETE FROM organization_units WHERE tenant_id = %s AND id = %s",
            (principal.tenant_id, organization_id),
        ).rowcount
        if deleted == 0:
            raise HTTPException(status_code=404, detail="not_found")
    return Response(status_code=204)


@router.get("/roles", response_model=RoleListResponse)
def list_roles(
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> RoleListResponse:
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        rows = connection.execute(
            """
            SELECT role.id, role.role_key, role.name, role.description,
                   role.is_system, role.is_active,
                   COALESCE(array_agg(permission.permission_key ORDER BY permission.permission_key)
                     FILTER (WHERE permission.permission_key IS NOT NULL), ARRAY[]::text[])
                     AS permissions
            FROM app_roles AS role
            LEFT JOIN app_role_permissions AS permission
              ON permission.tenant_id = role.tenant_id AND permission.role_id = role.id
            WHERE role.tenant_id = %s
            GROUP BY role.id, role.role_key, role.name, role.description,
                     role.is_system, role.is_active
            ORDER BY role.is_system DESC, role.name, role.id
            """,
            (principal.tenant_id,),
        ).fetchall()
    return RoleListResponse(
        roles=[RoleView(**row) for row in rows],
        permissions=[
            PermissionView(key=item.key, name=item.name, description=item.description)
            for item in PERMISSIONS
        ],
    )


@router.post("/roles", response_model=RoleView, status_code=201)
def create_role(
    body: RoleRequest,
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> RoleView:
    if not body.name:
        raise HTTPException(status_code=422, detail="role_invalid")
    role_key = f"custom_{uuid4().hex[:20]}"
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        role = connection.execute(
            "INSERT INTO app_roles "
            "(tenant_id, role_key, name, description, is_system, created_by, updated_by) "
            "VALUES (%s, %s, %s, %s, false, %s, %s) "
            "RETURNING id, role_key, name, description, is_system, is_active",
            (
                principal.tenant_id,
                role_key,
                body.name,
                body.description,
                principal.actor_id,
                principal.actor_id,
            ),
        ).fetchone()
        assert role is not None
        execute_many(
            connection,
            "INSERT INTO app_role_permissions (tenant_id, role_id, permission_key) "
            "VALUES (%s, %s, %s)",
            [(principal.tenant_id, role["id"], key) for key in body.permissions],
        )
    return RoleView(**role, permissions=body.permissions)


@router.patch("/roles/{role_id}", response_model=RoleView)
def update_role(
    role_id: UUID,
    body: RoleRequest,
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> RoleView:
    if not body.name:
        raise HTTPException(status_code=422, detail="role_invalid")
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        role = connection.execute(
            "UPDATE app_roles SET name = %s, description = %s, updated_by = %s, "
            "updated_at = clock_timestamp() WHERE tenant_id = %s AND id = %s "
            "AND is_system = false "
            "RETURNING id, role_key, name, description, is_system, is_active",
            (
                body.name,
                body.description,
                principal.actor_id,
                principal.tenant_id,
                role_id,
            ),
        ).fetchone()
        if role is None:
            raise HTTPException(status_code=404, detail="not_found")
        connection.execute(
            "DELETE FROM app_role_permissions WHERE tenant_id = %s AND role_id = %s",
            (principal.tenant_id, role_id),
        )
        execute_many(
            connection,
            "INSERT INTO app_role_permissions (tenant_id, role_id, permission_key) "
            "VALUES (%s, %s, %s)",
            [(principal.tenant_id, role_id, key) for key in body.permissions],
        )
    return RoleView(**role, permissions=body.permissions)


@router.delete("/roles/{role_id}", status_code=204)
def deactivate_role(
    role_id: UUID,
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> Response:
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        row = connection.execute(
            "UPDATE app_roles SET is_active = false, updated_by = %s, "
            "updated_at = clock_timestamp() WHERE tenant_id = %s AND id = %s "
            "AND is_system = false RETURNING id",
            (principal.actor_id, principal.tenant_id, role_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        connection.execute(
            "UPDATE app_user_roles SET revoked_by = %s, revoked_at = clock_timestamp() "
            "WHERE tenant_id = %s AND role_id = %s AND revoked_at IS NULL",
            (principal.actor_id, principal.tenant_id, role_id),
        )
    return Response(status_code=204)


@router.get("/users", response_model=UserListResponse)
def list_users(
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> UserListResponse:
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        rows = connection.execute(
            "SELECT actor_id FROM app_users WHERE tenant_id = %s "
            "ORDER BY display_name, username, actor_id",
            (principal.tenant_id,),
        ).fetchall()
        items = [
            _user_view(connection, principal.tenant_id, row["actor_id"]) for row in rows
        ]
    return UserListResponse(items=items)


@router.post("/users", response_model=UserView, status_code=201)
def create_user(
    body: CreateUserRequest,
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> UserView:
    username = body.username.strip()
    username_key = username.casefold()
    display_name = body.display_name.strip()
    if not username_key or not display_name:
        raise HTTPException(status_code=422, detail="user_invalid")
    tenant_id = principal.tenant_id
    actor_id = uuid4()
    password_hash = hash_password(body.initial_password)
    try:
        with psycopg.connect(database_url(), row_factory=dict_row) as connection:
            set_tenant(connection, tenant_id)
            if not _active_organization_exists(connection, tenant_id, body.organization_id):
                raise HTTPException(status_code=422, detail="organization_invalid")
            role_ids = _validate_role_ids(connection, tenant_id, body.role_ids)
            connection.execute(
                "INSERT INTO app_users "
                "(tenant_id, actor_id, username, username_key, display_name, organization_id, "
                "password_hash, must_change_password, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s)",
                (
                    tenant_id,
                    actor_id,
                    username,
                    username_key,
                    display_name,
                    body.organization_id,
                    password_hash,
                    principal.actor_id,
                ),
            )
            execute_many(
                connection,
                "INSERT INTO app_user_roles (tenant_id, actor_id, role_id, granted_by) "
                "VALUES (%s, %s, %s, %s)",
                [
                    (tenant_id, actor_id, role_id, principal.actor_id)
                    for role_id in role_ids
                ],
            )
            return _user_view(connection, tenant_id, actor_id)
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="username_exists") from exc


@router.patch("/users/{actor_id}", response_model=UserView)
def update_user(
    actor_id: UUID,
    body: UpdateUserRequest,
    principal: Annotated[Principal, Depends(require_permission("admin.manage"))],
) -> UserView:
    tenant_id = principal.tenant_id
    display_name = body.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="user_invalid")
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, tenant_id)
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"app_admins:{tenant_id}",),
        )
        role_ids = _validate_role_ids(connection, tenant_id, body.role_ids)
        if not _active_organization_exists(connection, tenant_id, body.organization_id):
            raise HTTPException(status_code=422, detail="organization_invalid")
        existing = connection.execute(
            "SELECT 1 FROM app_users WHERE tenant_id = %s AND actor_id = %s FOR UPDATE",
            (tenant_id, actor_id),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="not_found")
        connection.execute(
            "UPDATE app_users SET display_name = %s, organization_id = %s, is_active = %s, "
            "password_hash = COALESCE(%s, password_hash), must_change_password = "
            "CASE WHEN %s IS NULL THEN must_change_password ELSE true END, "
            "updated_at = clock_timestamp() WHERE tenant_id = %s AND actor_id = %s",
            (
                display_name,
                body.organization_id,
                body.is_active,
                hash_password(body.new_password) if body.new_password else None,
                body.new_password,
                tenant_id,
                actor_id,
            ),
        )
        connection.execute(
            "UPDATE app_user_roles SET revoked_by = %s, revoked_at = clock_timestamp() "
            "WHERE tenant_id = %s AND actor_id = %s AND revoked_at IS NULL",
            (principal.actor_id, tenant_id, actor_id),
        )
        execute_many(
            connection,
            "INSERT INTO app_user_roles (tenant_id, actor_id, role_id, granted_by) "
            "VALUES (%s, %s, %s, %s)",
            [
                (tenant_id, actor_id, role_id, principal.actor_id)
                for role_id in role_ids
            ],
        )
        if not body.is_active or body.new_password:
            connection.execute(
                "UPDATE app_sessions SET revoked_at = clock_timestamp() "
                "WHERE tenant_id = %s AND actor_id = %s AND revoked_at IS NULL",
                (tenant_id, actor_id),
            )
        if not _has_active_system_administrator(connection, tenant_id):
            raise HTTPException(status_code=409, detail="last_system_administrator_required")
        return _user_view(connection, tenant_id, actor_id)
