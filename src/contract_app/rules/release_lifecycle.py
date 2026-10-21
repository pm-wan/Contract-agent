"""Fail-closed rule release lifecycle transitions."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReleaseState = Literal[
    "draft", "review", "evaluated", "approved", "published", "deprecated", "suspended", "retired"
]
ReleaseRole = Literal["author", "reviewer", "evaluator", "approver", "operator"]

_TRANSITIONS: dict[ReleaseState, frozenset[ReleaseState]] = {
    "draft": frozenset({"review"}),
    "review": frozenset({"evaluated"}),
    "evaluated": frozenset({"approved"}),
    "approved": frozenset({"published"}),
    "published": frozenset({"deprecated", "suspended", "retired"}),
    "deprecated": frozenset({"retired"}),
    "suspended": frozenset({"review", "retired"}),
    "retired": frozenset(),
}
_ROLE_TRANSITIONS: dict[ReleaseRole, frozenset[ReleaseState]] = {
    "author": frozenset({"review"}),
    "reviewer": frozenset({"evaluated"}),
    "evaluator": frozenset({"evaluated"}),
    "approver": frozenset({"approved", "published"}),
    "operator": frozenset({"deprecated", "suspended", "retired", "review"}),
}


class ReleaseLifecycleInvalid(ValueError):
    """A release transition is invalid or lacks a publishable audit."""


class ReleaseArtifactIdentity(BaseModel):
    """Immutable identities that a release decision must bind."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    skill_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    knowledge_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation_report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    audit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReleaseTransition(BaseModel):
    """A content-addressed lifecycle decision; it does not mutate storage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["rule_release_transition"]
    rule_id: str = Field(min_length=1, max_length=160)
    rule_version: str = Field(min_length=1, max_length=64)
    from_state: ReleaseState
    to_state: ReleaseState
    actor_role: ReleaseRole
    actor_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1000)
    rollback_target: str | None = Field(default=None, max_length=64)
    artifacts: ReleaseArtifactIdentity
    transition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_hash(self) -> ReleaseTransition:
        unsigned = self.model_dump(mode="json", exclude={"transition_sha256"})
        expected = _canonical_sha256(unsigned)
        if self.transition_sha256 != expected:
            raise ValueError("release_transition_hash_mismatch")
        return self


def validate_release_transition(
    current_state: ReleaseState,
    transition: ReleaseTransition,
    *,
    audit_release_ready: bool,
) -> ReleaseState:
    """Validate one transition against lifecycle, role and release-gate rules."""
    if transition.from_state != current_state:
        raise ReleaseLifecycleInvalid("release_transition_state_mismatch")
    if transition.to_state not in _TRANSITIONS[current_state]:
        raise ReleaseLifecycleInvalid("release_transition_not_allowed")
    if transition.to_state not in _ROLE_TRANSITIONS[transition.actor_role]:
        raise ReleaseLifecycleInvalid("release_transition_role_not_allowed")
    if transition.to_state in {"approved", "published"} and not audit_release_ready:
        raise ReleaseLifecycleInvalid("release_transition_audit_blocked")
    if transition.to_state == "suspended" and not transition.reason.strip():
        raise ReleaseLifecycleInvalid("release_transition_reason_required")
    if transition.to_state == "published" and not transition.rollback_target:
        raise ReleaseLifecycleInvalid("release_transition_rollback_target_required")
    return transition.to_state


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
