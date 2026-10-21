"""Local username/password authentication and session management."""

from __future__ import annotations

import os
import secrets
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator

from contract_app.api_errors import COMMON_PROBLEM_RESPONSES
from contract_app.auth_catalog import BUILTIN_ROLES
from contract_app.auth_security import (
    SESSION_COOKIE_NAME,
    Principal,
    configured_tenant_id,
    database_url,
    execute_many,
    hash_password,
    require_principal,
    session_token_hash,
    set_tenant,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"], responses=COMMON_PROBLEM_RESPONSES)
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def trim_username(cls, value: str) -> str:
        return value.strip()


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AuthProfileResponse(BaseModel):
    actor_id: UUID
    username: str
    display_name: str
    organization_id: UUID
    organization_name: str
    roles: list[str]
    permissions: list[str]
    must_change_password: bool


def _ensure_builtin_roles(connection: psycopg.Connection, tenant_id: UUID) -> dict[str, UUID]:
    role_ids: dict[str, UUID] = {}
    for role_definition in BUILTIN_ROLES:
        role = connection.execute(
            """
            INSERT INTO app_roles (tenant_id, role_key, name, description, is_system)
            VALUES (%s, %s, %s, %s, true)
            ON CONFLICT (tenant_id, role_key) DO UPDATE
              SET name = EXCLUDED.name, description = EXCLUDED.description,
                  is_system = true, is_active = true, updated_at = clock_timestamp()
            RETURNING id
            """,
            (
                tenant_id,
                role_definition.key,
                role_definition.name,
                role_definition.description,
            ),
        ).fetchone()
        assert role is not None
        role_id = role["id"]
        role_ids[role_definition.key] = role_id
        connection.execute(
            "DELETE FROM app_role_permissions WHERE tenant_id = %s AND role_id = %s",
            (tenant_id, role_id),
        )
        execute_many(
            connection,
            "INSERT INTO app_role_permissions (tenant_id, role_id, permission_key) "
            "VALUES (%s, %s, %s)",
            [
                (tenant_id, role_id, permission_key)
                for permission_key in sorted(role_definition.permission_keys)
            ],
        )
    return role_ids


def _profile(
    connection: psycopg.Connection,
    tenant_id: UUID,
    actor_id: UUID,
) -> AuthProfileResponse:
    user = connection.execute(
        """
        SELECT account.actor_id, account.username, account.display_name,
               account.organization_id, organization.name AS organization_name,
               account.must_change_password
        FROM app_users AS account
        JOIN organization_units AS organization
          ON organization.tenant_id = account.tenant_id
         AND organization.id = account.organization_id
        WHERE account.tenant_id = %s AND account.actor_id = %s AND account.is_active
        """,
        (tenant_id, actor_id),
    ).fetchone()
    if user is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    role_rows = connection.execute(
        """
        SELECT DISTINCT role.role_key, permission.permission_key
        FROM app_user_roles AS assignment
        JOIN app_roles AS role
          ON role.tenant_id = assignment.tenant_id AND role.id = assignment.role_id
        LEFT JOIN app_role_permissions AS permission
          ON permission.tenant_id = role.tenant_id AND permission.role_id = role.id
        WHERE assignment.tenant_id = %s AND assignment.actor_id = %s
          AND assignment.revoked_at IS NULL AND role.is_active
        ORDER BY role.role_key, permission.permission_key
        """,
        (tenant_id, actor_id),
    ).fetchall()
    return AuthProfileResponse(
        actor_id=user["actor_id"],
        username=user["username"],
        display_name=user["display_name"],
        organization_id=user["organization_id"],
        organization_name=user["organization_name"],
        roles=sorted({row["role_key"] for row in role_rows}),
        permissions=sorted(
            {row["permission_key"] for row in role_rows if row["permission_key"]}
        ),
        must_change_password=user["must_change_password"],
    )


def _set_session_cookie(response: Response, token: str) -> None:
    secure_setting = os.environ.get("SESSION_COOKIE_SECURE")
    secure = (
        secure_setting.lower() in {"1", "true", "yes"}
        if secure_setting is not None
        else os.environ.get("APP_ENV") == "production"
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _new_session(
    connection: psycopg.Connection,
    tenant_id: UUID,
    actor_id: UUID,
) -> str:
    token = secrets.token_urlsafe(32)
    connection.execute(
        "INSERT INTO app_sessions (tenant_id, token_sha256, actor_id, expires_at) "
        "VALUES (%s, %s, %s, clock_timestamp() + interval '12 hours')",
        (tenant_id, session_token_hash(token), actor_id),
    )
    connection.execute(
        "UPDATE app_users SET last_login_at = clock_timestamp() "
        "WHERE tenant_id = %s AND actor_id = %s",
        (tenant_id, actor_id),
    )
    return token


@router.post("/login", response_model=AuthProfileResponse)
def login(body: LoginRequest, response: Response) -> AuthProfileResponse:
    tenant_id = configured_tenant_id()
    username_key = body.username.strip().casefold()
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, tenant_id)
        account = connection.execute(
            "SELECT actor_id, password_hash FROM app_users "
            "WHERE tenant_id = %s AND username_key = %s AND is_active",
            (tenant_id, username_key),
        ).fetchone()
        if account is None or not verify_password(body.password, account["password_hash"]):
            raise HTTPException(status_code=401, detail="invalid_credentials")
        token = _new_session(connection, tenant_id, account["actor_id"])
        profile = _profile(connection, tenant_id, account["actor_id"])
    _set_session_cookie(response, token)
    return profile


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        tenant_id = configured_tenant_id()
        with psycopg.connect(database_url(), row_factory=dict_row) as connection:
            set_tenant(connection, tenant_id)
            connection.execute(
                "UPDATE app_sessions SET revoked_at = clock_timestamp() "
                "WHERE tenant_id = %s AND token_sha256 = %s AND revoked_at IS NULL",
                (tenant_id, session_token_hash(token)),
            )
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, samesite="lax")
    return response


@router.get("/me", response_model=AuthProfileResponse)
def current_profile(
    principal: Annotated[Principal, Depends(require_principal)],
) -> AuthProfileResponse:
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        return _profile(connection, principal.tenant_id, principal.actor_id)


@router.post("/change-password", response_model=AuthProfileResponse)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_principal)],
) -> AuthProfileResponse:
    if body.current_password == body.new_password:
        raise HTTPException(status_code=422, detail="password_unchanged")
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie_token:
        raise HTTPException(status_code=401, detail="authentication_required")
    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        user = connection.execute(
            "SELECT password_hash FROM app_users "
            "WHERE tenant_id = %s AND actor_id = %s AND is_active",
            (principal.tenant_id, principal.actor_id),
        ).fetchone()
        if user is None or not verify_password(body.current_password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="current_password_invalid")
        connection.execute(
            "UPDATE app_users SET password_hash = %s, must_change_password = false, "
            "updated_at = clock_timestamp() WHERE tenant_id = %s AND actor_id = %s",
            (hash_password(body.new_password), principal.tenant_id, principal.actor_id),
        )
        connection.execute(
            "UPDATE app_sessions SET revoked_at = clock_timestamp() "
            "WHERE tenant_id = %s AND actor_id = %s AND revoked_at IS NULL",
            (principal.tenant_id, principal.actor_id),
        )
        token = _new_session(connection, principal.tenant_id, principal.actor_id)
        profile = _profile(connection, principal.tenant_id, principal.actor_id)
    _set_session_cookie(response, token)
    return profile
