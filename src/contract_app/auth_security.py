"""Local account sessions and server-side feature authorization."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Callable
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException, Request
from psycopg.rows import dict_row

from contract_app.oidc_auth import (
    InvalidOIDCToken,
    OIDCUnavailable,
    configured_oidc_verifier,
)

SESSION_COOKIE_NAME = "contract_audit_session"


def execute_many(
    connection: psycopg.Connection,
    query: str,
    parameters: Iterable[Sequence[Any]],
) -> None:
    """Execute a batch with a short-lived psycopg cursor."""
    with connection.cursor() as cursor:
        cursor.executemany(query, parameters)


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: UUID
    actor_id: UUID
    roles: tuple[str, ...] = ()
    permissions: frozenset[str] = frozenset()


def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return value


def configured_tenant_id() -> UUID:
    raw_value = (
        os.environ.get("AUTH_TENANT_ID")
        or os.environ.get("DEVELOPMENT_AUTH_TENANT_ID")
        or "00000000-0000-4000-8000-000000000001"
    )
    try:
        return UUID(raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="service_unavailable") from exc


def set_tenant(connection: psycopg.Connection, tenant_id: UUID) -> None:
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64
    )
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"scrypt$16384$8$1${salt_text}${digest_text}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = password_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
            dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _roles_and_permissions(
    connection: psycopg.Connection, tenant_id: UUID, actor_id: UUID
) -> tuple[tuple[str, ...], frozenset[str]]:
    rows = connection.execute(
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
    roles = tuple(sorted({row["role_key"] for row in rows}))
    permissions = frozenset(row["permission_key"] for row in rows if row["permission_key"])
    return roles, permissions


def _principal_for_user(
    connection: psycopg.Connection, tenant_id: UUID, actor_id: UUID
) -> Principal:
    user = connection.execute(
        "SELECT 1 FROM app_users WHERE tenant_id = %s AND actor_id = %s AND is_active",
        (tenant_id, actor_id),
    ).fetchone()
    if user is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    roles, permissions = _roles_and_permissions(connection, tenant_id, actor_id)
    return Principal(tenant_id, actor_id, roles, permissions)


def require_principal(request: Request) -> Principal:
    """Resolve an active local account session or an explicitly configured OIDC user."""
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        tenant_id = configured_tenant_id()
        with psycopg.connect(database_url(), row_factory=dict_row) as connection:
            set_tenant(connection, tenant_id)
            row = connection.execute(
                """
                SELECT session.actor_id
                FROM app_sessions AS session
                JOIN app_users AS account
                  ON account.tenant_id = session.tenant_id
                 AND account.actor_id = session.actor_id
                WHERE session.tenant_id = %s AND session.token_sha256 = %s
                  AND session.revoked_at IS NULL AND session.expires_at > clock_timestamp()
                  AND account.is_active
                """,
                (tenant_id, session_token_hash(cookie_token)),
            ).fetchone()
            if row is not None:
                return _principal_for_user(connection, tenant_id, row["actor_id"])

    if os.environ.get("CONTRACT_AUTH_MODE") == "oidc":
        issuer = os.environ.get("OIDC_ISSUER", "")
        audience = os.environ.get("OIDC_AUDIENCE", "")
        jwks_url = os.environ.get("OIDC_JWKS_URL", "")
        tenant_claim = os.environ.get("OIDC_TENANT_CLAIM", "tenant_id")
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if not issuer or not audience or not jwks_url:
            raise HTTPException(status_code=503, detail="identity_provider_unavailable")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="authentication_required")
        try:
            identity = configured_oidc_verifier(
                issuer, audience, jwks_url, tenant_claim
            ).verify(token)
        except InvalidOIDCToken as exc:
            raise HTTPException(status_code=401, detail="authentication_required") from exc
        except (OIDCUnavailable, ValueError) as exc:
            raise HTTPException(status_code=503, detail="identity_provider_unavailable") from exc
        with psycopg.connect(database_url(), row_factory=dict_row) as connection:
            set_tenant(connection, identity.tenant_id)
            return _principal_for_user(connection, identity.tenant_id, identity.actor_id)

    raise HTTPException(status_code=401, detail="authentication_required")


def require_permission(permission_key: str) -> Callable[..., Principal]:
    def dependency(
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> Principal:
        if permission_key not in principal.permissions:
            raise HTTPException(status_code=403, detail="permission_required")
        return principal

    return dependency


def require_any_permission(*permission_keys: str) -> Callable[..., Principal]:
    def dependency(
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> Principal:
        if not principal.permissions.intersection(permission_keys):
            raise HTTPException(status_code=403, detail="permission_required")
        return principal

    return dependency
