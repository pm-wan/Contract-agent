"""Issue fail-closed completion proofs from explicitly approved policies."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contract_app.workflow.dependency_result_reference import DependencyResultReference

_ASSESSMENT_STATUSES = frozenset(
    {"not_applicable", "blocked", "undetermined", "incomplete", "needs_material", "human_review"}
)
_JUDGMENTS = frozenset(
    {"passed", "issue_found", "needs_material", "undetermined", "not_applicable", "human_review"}
)
_CURRENTLY_COMPLETABLE_ASSESSMENTS = frozenset({"not_applicable"})


class DependencyCompletionProofInvalid(ValueError):
    """A dependency result cannot be upgraded into a completion proof."""


class ApprovedCompletionPolicy(BaseModel):
    """A content-addressed policy supplied by an external approval process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["approved_dependency_completion_policy"]
    policy_id: str = Field(min_length=1, max_length=160)
    policy_version: str = Field(min_length=1, max_length=64)
    allowed_assessment_statuses: tuple[str, ...] = Field(min_length=1, max_length=6)
    allowed_judgments: tuple[str, ...] = Field(min_length=1, max_length=6)
    approval_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approver_role: str = Field(min_length=1, max_length=160)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_policy(self) -> ApprovedCompletionPolicy:
        if len(set(self.allowed_assessment_statuses)) != len(self.allowed_assessment_statuses):
            raise ValueError("completion_policy_statuses_duplicate")
        if len(set(self.allowed_judgments)) != len(self.allowed_judgments):
            raise ValueError("completion_policy_judgments_duplicate")
        if any(status not in _ASSESSMENT_STATUSES for status in self.allowed_assessment_statuses):
            raise ValueError("completion_policy_assessment_status_invalid")
        if not set(self.allowed_assessment_statuses).issubset(
            _CURRENTLY_COMPLETABLE_ASSESSMENTS
        ):
            raise ValueError("completion_policy_assessment_status_not_terminal")
        if any(judgment not in _JUDGMENTS for judgment in self.allowed_judgments):
            raise ValueError("completion_policy_judgment_invalid")
        unsigned = self.model_dump(mode="json", exclude={"policy_sha256"})
        expected = _canonical_sha256(unsigned)
        if self.policy_sha256 != expected:
            raise ValueError("completion_policy_hash_mismatch")
        return self


class DependencyCompletionProof(BaseModel):
    """An immutable proof that one result satisfied an approved policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["dependency_completion_proof"]
    check_id: str = Field(min_length=1, max_length=160)
    reference_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_id: str = Field(min_length=1, max_length=160)
    policy_version: str = Field(min_length=1, max_length=64)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: ApprovedCompletionPolicy
    source_run_id: str = Field(min_length=1, max_length=64)
    result_id: str = Field(min_length=1, max_length=64)
    observed_judgment: str
    assessment_status: str
    is_completion_proof: Literal[True]
    proof_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_proof_hash(self) -> DependencyCompletionProof:
        unsigned = self.model_dump(mode="json", exclude={"proof_sha256"})
        if self.proof_sha256 != _canonical_sha256(unsigned):
            raise ValueError("dependency_completion_proof_hash_mismatch")
        if (
            self.policy.policy_id != self.policy_id
            or self.policy.policy_version != self.policy_version
            or self.policy.policy_sha256 != self.policy_sha256
        ):
            raise ValueError("dependency_completion_policy_identity_mismatch")
        if self.assessment_status not in self.policy.allowed_assessment_statuses:
            raise ValueError("dependency_completion_status_not_allowed")
        if self.observed_judgment not in self.policy.allowed_judgments:
            raise ValueError("dependency_completion_judgment_not_allowed")
        return self


def issue_dependency_completion_proof(
    reference: DependencyResultReference,
    policy: ApprovedCompletionPolicy,
) -> DependencyCompletionProof:
    """Upgrade one reference only when an approved policy explicitly allows it."""
    if reference.is_completion_proof:
        raise DependencyCompletionProofInvalid("dependency_reference_already_upgraded")
    if reference.assessment_status not in policy.allowed_assessment_statuses:
        raise DependencyCompletionProofInvalid("dependency_completion_status_not_allowed")
    if reference.observed_judgment not in policy.allowed_judgments:
        raise DependencyCompletionProofInvalid("dependency_completion_judgment_not_allowed")
    content = {
        "kind": "dependency_completion_proof",
        "check_id": reference.check_id,
        "reference_sha256": reference.reference_sha256,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_sha256": policy.policy_sha256,
        "policy": policy.model_dump(mode="json"),
        "source_run_id": str(reference.source_run_id),
        "result_id": str(reference.result_id),
        "observed_judgment": reference.observed_judgment,
        "assessment_status": reference.assessment_status,
        "is_completion_proof": True,
    }
    return DependencyCompletionProof(
        kind="dependency_completion_proof",
        check_id=reference.check_id,
        reference_sha256=reference.reference_sha256,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy.policy_sha256,
        policy=policy,
        source_run_id=str(reference.source_run_id),
        result_id=str(reference.result_id),
        observed_judgment=reference.observed_judgment,
        assessment_status=reference.assessment_status,
        is_completion_proof=True,
        proof_sha256=_canonical_sha256(content),
    )


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
