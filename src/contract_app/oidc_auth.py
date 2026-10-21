"""OIDC JWT verification and stable subject-to-actor mapping.

The identity provider proves who the caller is. Case membership in PostgreSQL
remains the sole source of application roles and resource authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWTError


class SigningKeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class OIDCIdentity:
    tenant_id: UUID
    actor_id: UUID
    issuer: str
    subject: str


class OIDCUnavailable(RuntimeError):
    """The configured identity provider cannot currently validate a token."""


class InvalidOIDCToken(ValueError):
    """A bearer token is not a valid token for this API."""


def validate_oidc_url(value: str, *, setting: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{setting}_invalid")
    return value


class OIDCVerifier:
    """Verify OIDC access tokens against a pinned issuer, audience and JWKS URL."""

    algorithms = ("RS256", "ES256")

    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
        tenant_claim: str = "tenant_id",
        *,
        key_resolver: SigningKeyResolver | None = None,
    ) -> None:
        self.issuer = validate_oidc_url(issuer, setting="OIDC_ISSUER")
        self.audience = audience.strip()
        if not self.audience or len(self.audience) > 256:
            raise ValueError("OIDC_AUDIENCE_invalid")
        self.jwks_url = validate_oidc_url(jwks_url, setting="OIDC_JWKS_URL")
        if not tenant_claim or len(tenant_claim) > 128 or "." in tenant_claim:
            raise ValueError("OIDC_TENANT_CLAIM_invalid")
        self.tenant_claim = tenant_claim
        self.key_resolver = key_resolver or PyJWKClient(
            self.jwks_url,
            cache_jwk_set=True,
            lifespan=300,
            timeout=3,
            cooldown_duration=0,
        )

    def verify(self, token: str) -> OIDCIdentity:
        if not token or len(token) > 16_384:
            raise InvalidOIDCToken("token_invalid")
        try:
            signing_key = self.key_resolver.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                getattr(signing_key, "key", signing_key),
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                leeway=30,
                options={"require": ["exp", "iat", "sub"]},
            )
        except PyJWKClientConnectionError as exc:
            raise OIDCUnavailable("oidc_jwks_unavailable") from exc
        except (PyJWKClientError, PyJWTError, TypeError, ValueError) as exc:
            raise InvalidOIDCToken("token_invalid") from exc

        subject = claims.get("sub")
        raw_tenant = claims.get(self.tenant_claim)
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 255
            or subject != subject.strip()
            or not isinstance(raw_tenant, str)
        ):
            raise InvalidOIDCToken("token_claims_invalid")
        try:
            tenant_id = UUID(raw_tenant)
        except ValueError as exc:
            raise InvalidOIDCToken("token_claims_invalid") from exc

        # Membership provisioning can reproduce this ID. Email, display name,
        # and provider role claims are deliberately not used as authorization.
        actor_id = uuid5(NAMESPACE_URL, f"contract-audit:oidc:{self.issuer}\0{subject}")
        return OIDCIdentity(tenant_id, actor_id, self.issuer, subject)


@lru_cache(maxsize=8)
def configured_oidc_verifier(
    issuer: str,
    audience: str,
    jwks_url: str,
    tenant_claim: str,
) -> OIDCVerifier:
    return OIDCVerifier(issuer, audience, jwks_url, tenant_claim)
