import os
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from contract_app.api import create_app
from contract_app.api_cases import get_database_url
from contract_app.api_snapshots import Principal, require_principal


def test_development_identity_requires_explicit_local_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "local-only-development-token-1234567890"
    tenant_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    actor_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CONTRACT_AUTH_MODE", "development")
    monkeypatch.setenv("DEVELOPMENT_AUTH_TOKEN", token)
    monkeypatch.setenv("DEVELOPMENT_AUTH_TENANT_ID", str(tenant_id))
    monkeypatch.setenv("DEVELOPMENT_AUTH_ACTOR_ID", str(actor_id))

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert require_principal(credentials) == Principal(tenant_id, actor_id)


@pytest.mark.parametrize(
    ("app_env", "auth_mode", "token", "tenant", "actor"),
    [
        ("production", "development", "local-only-development-token-1234567890", "a", "b"),
        ("development", "unknown", "local-only-development-token-1234567890", "a", "b"),
        ("development", "development", "wrong-token", "a", "b"),
        ("development", "development", "local-only-development-token-1234567890", "bad", "b"),
    ],
)
def test_development_identity_fails_closed_outside_its_exact_mode(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    auth_mode: str,
    token: str,
    tenant: str,
    actor: str,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("CONTRACT_AUTH_MODE", auth_mode)
    monkeypatch.setenv("DEVELOPMENT_AUTH_TOKEN", token)
    monkeypatch.setenv("DEVELOPMENT_AUTH_TENANT_ID", tenant)
    monkeypatch.setenv("DEVELOPMENT_AUTH_ACTOR_ID", actor)

    with pytest.raises(HTTPException) as error:
        require_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert error.value.status_code == 401


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is required")
def test_development_bearer_is_resolved_through_http_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "local-only-development-token-1234567890"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CONTRACT_AUTH_MODE", "development")
    monkeypatch.setenv("DEVELOPMENT_AUTH_TOKEN", token)
    monkeypatch.setenv("DEVELOPMENT_AUTH_TENANT_ID", str(uuid4()))
    monkeypatch.setenv("DEVELOPMENT_AUTH_ACTOR_ID", str(uuid4()))

    app = create_app()
    app.dependency_overrides[get_database_url] = lambda: os.environ["TEST_DATABASE_URL"]
    client = TestClient(app)

    accepted = client.get("/api/v1/cases", headers={"Authorization": f"Bearer {token}"})
    rejected = client.get("/api/v1/cases", headers={"Authorization": "Bearer wrong-local-token"})
    assert accepted.status_code == 200
    assert accepted.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}
    assert rejected.status_code == 401
