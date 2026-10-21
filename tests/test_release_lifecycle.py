import json
from hashlib import sha256

import pytest

from contract_app.rules.release_lifecycle import (
    ReleaseArtifactIdentity,
    ReleaseLifecycleInvalid,
    ReleaseTransition,
    validate_release_transition,
)


def _transition(
    *, from_state: str, to_state: str, role: str, reason: str = "reviewed"
) -> ReleaseTransition:
    artifacts = ReleaseArtifactIdentity(
        rule_sha256="a" * 64,
        skill_sha256="b" * 64,
        knowledge_sha256="c" * 64,
        evaluation_report_sha256="d" * 64,
        audit_sha256="e" * 64,
    )
    content = {
        "kind": "rule_release_transition",
        "rule_id": "legal.payment_conditions",
        "rule_version": "1.0.0",
        "from_state": from_state,
        "to_state": to_state,
        "actor_role": role,
        "actor_id": "actor-1",
        "reason": reason,
        "rollback_target": "1.0.0" if to_state == "published" else None,
        "artifacts": artifacts.model_dump(mode="json"),
    }
    digest = sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ReleaseTransition(**content, transition_sha256=digest)


def test_published_transition_requires_a_passing_audit() -> None:
    transition = _transition(from_state="approved", to_state="published", role="approver")
    with pytest.raises(ReleaseLifecycleInvalid, match="release_transition_audit_blocked"):
        validate_release_transition("approved", transition, audit_release_ready=False)


def test_author_cannot_approve_a_release() -> None:
    transition = _transition(from_state="evaluated", to_state="approved", role="author")
    with pytest.raises(ReleaseLifecycleInvalid, match="release_transition_role_not_allowed"):
        validate_release_transition("evaluated", transition, audit_release_ready=True)


def test_published_transition_requires_rollback_target() -> None:
    transition = _transition(from_state="approved", to_state="published", role="approver")
    changed = transition.model_copy(update={"rollback_target": None})
    with pytest.raises(ValueError, match="release_transition_hash_mismatch"):
        ReleaseTransition.model_validate(changed.model_dump(mode="json"))
