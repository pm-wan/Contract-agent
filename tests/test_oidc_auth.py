from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jwt.exceptions import PyJWKClientConnectionError

from contract_app import api_snapshots
from contract_app.oidc_auth import (
    InvalidOIDCToken,
    OIDCUnavailable,
    OIDCVerifier,
)

ISSUER = "https://identity.example.test"
AUDIENCE = "contract-audit-api"
JWKS_URL = "https://identity.example.test/.well-known/jwks.json"
TENANT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class StaticKeyResolver:
    def __init__(self, key: Any) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return SimpleNamespace(key=self.key)


@pytest.fixture
def rsa_keypair() -> tuple[Any, Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _token(private_key: Any, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-42",
        "tenant_id": str(TENANT),
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def _verifier(public_key: Any) -> OIDCVerifier:
    return OIDCVerifier(
        ISSUER,
        AUDIENCE,
        JWKS_URL,
        key_resolver=StaticKeyResolver(public_key),
    )


def test_oidc_verifier_checks_signature_issuer_audience_and_claims(
    rsa_keypair: tuple[Any, Any],
) -> None:
    private_key, public_key = rsa_keypair
    identity = _verifier(public_key).verify(_token(private_key))

    assert identity.tenant_id == TENANT
    assert identity.subject == "user-42"
    assert identity.issuer == ISSUER
    assert identity.actor_id == uuid5(
        NAMESPACE_URL,
        f"contract-audit:oidc:{ISSUER}\0user-42",
    )
    assert _verifier(public_key).verify(_token(private_key)).actor_id == identity.actor_id


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.example.test"},
        {"aud": "another-api"},
        {"exp": datetime.now(UTC) - timedelta(minutes=2)},
        {"tenant_id": "not-a-uuid"},
        {"sub": " "},
    ],
)
def test_oidc_verifier_rejects_invalid_registered_or_tenant_claims(
    rsa_keypair: tuple[Any, Any], claims: dict[str, Any]
) -> None:
    private_key, public_key = rsa_keypair
    with pytest.raises(InvalidOIDCToken):
        _verifier(public_key).verify(_token(private_key, **claims))


def test_oidc_verifier_rejects_wrong_signature_and_algorithm(rsa_keypair: tuple[Any, Any]) -> None:
    _, public_key = rsa_keypair
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(InvalidOIDCToken):
        _verifier(public_key).verify(_token(other_private_key))

    unsigned = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "user-42",
            "tenant_id": str(TENANT),
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(InvalidOIDCToken):
        _verifier(public_key).verify(unsigned)


def test_oidc_verifier_distinguishes_jwks_outage_from_bad_token() -> None:
    class UnavailableResolver:
        def get_signing_key_from_jwt(self, token: str) -> Any:
            raise PyJWKClientConnectionError("network unavailable")

    verifier = OIDCVerifier(
        ISSUER,
        AUDIENCE,
        JWKS_URL,
        key_resolver=UnavailableResolver(),
    )
    with pytest.raises(OIDCUnavailable):
        verifier.verify("a.b.c")


@pytest.mark.parametrize(
    ("issuer", "jwks_url"),
    [
        ("http://identity.example.test", JWKS_URL),
        (ISSUER, "http://identity.example.test/jwks"),
        ("https://user:password@identity.example.test", JWKS_URL),
    ],
)
def test_oidc_verifier_rejects_insecure_or_ambiguous_urls(issuer: str, jwks_url: str) -> None:
    with pytest.raises(ValueError):
        OIDCVerifier(issuer, AUDIENCE, jwks_url)


def test_oidc_auth_mode_resolves_principal_from_verified_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = SimpleNamespace(tenant_id=TENANT, actor_id=UUID(int=77))
    monkeypatch.setenv("CONTRACT_AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setattr(
        api_snapshots,
        "configured_oidc_verifier",
        lambda *_args: SimpleNamespace(verify=lambda token: identity),
    )

    principal = api_snapshots.require_principal(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="verified-token")
    )
    assert principal == api_snapshots.Principal(TENANT, identity.actor_id)


def test_oidc_auth_mode_fails_closed_when_configuration_or_identity_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTRACT_AUTH_MODE", "oidc")
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OIDC_JWKS_URL", JWKS_URL)
    with pytest.raises(HTTPException) as missing_configuration:
        api_snapshots.require_principal(None)
    assert missing_configuration.value.status_code == 503

    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setattr(
        api_snapshots,
        "configured_oidc_verifier",
        lambda *_args: SimpleNamespace(
            verify=lambda _token: (_ for _ in ()).throw(InvalidOIDCToken())
        ),
    )
    with pytest.raises(HTTPException) as invalid_token:
        api_snapshots.require_principal(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-token")
        )
    assert invalid_token.value.status_code == 401
