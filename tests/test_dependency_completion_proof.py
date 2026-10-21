import json
from hashlib import sha256

import pytest

from contract_app.workflow.dependency_completion_proof import (
    ApprovedCompletionPolicy,
    DependencyCompletionProofInvalid,
    issue_dependency_completion_proof,
)
from contract_app.workflow.dependency_result_reference import DependencyResultReference

CASE_ID = "11111111-1111-4111-8111-111111111111"
INPUT_SNAPSHOT_ID = "22222222-2222-4222-8222-222222222222"
RULE_ID = "legal.price.definition"
RULE_VERSION = "1.0.0"
RULE_SHA256 = "a" * 64


def _policy() -> ApprovedCompletionPolicy:
    content = {
        "kind": "approved_dependency_completion_policy",
        "policy_id": "qa.dependency-completion",
        "policy_version": "1.0.0",
        "allowed_assessment_statuses": ["not_applicable"],
        "allowed_judgments": ["not_applicable"],
        "approval_record_sha256": "e" * 64,
        "approver_role": "qa-approver",
    }
    digest = sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ApprovedCompletionPolicy(**content, policy_sha256=digest)


def test_completion_policy_rejects_nonterminal_assessment_states() -> None:
    content = {
        "kind": "approved_dependency_completion_policy",
        "policy_id": "qa.dependency-completion",
        "policy_version": "1.0.0",
        "allowed_assessment_statuses": ["blocked"],
        "allowed_judgments": ["passed"],
        "approval_record_sha256": "e" * 64,
        "approver_role": "qa-approver",
    }
    digest = sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="completion_policy_assessment_status_not_terminal"):
        ApprovedCompletionPolicy(**content, policy_sha256=digest)


def test_blocked_dependency_reference_cannot_be_upgraded() -> None:
    content = {
        "kind": "dependency_result_reference",
        "check_id": RULE_ID,
        "dependency_rule_id": RULE_ID,
        "dependency_rule_version": RULE_VERSION,
        "dependency_rule_sha256": RULE_SHA256,
        "definition_manifest_sha256": "b" * 64,
        "case_id": CASE_ID,
        "input_snapshot_id": INPUT_SNAPSHOT_ID,
        "fact_set_snapshot_id": None,
        "source_run_id": "33333333-3333-4333-8333-333333333333",
        "result_id": "44444444-4444-4444-8444-444444444444",
        "task_result_sha256": "c" * 64,
        "assessment_sha256": "d" * 64,
        "evidence_manifest_sha256": "e" * 64,
        "observed_judgment": "undetermined",
        "assessment_status": "blocked",
        "is_completion_proof": False,
    }
    reference = DependencyResultReference(
        **content,
        reference_sha256=sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    with pytest.raises(
        DependencyCompletionProofInvalid, match="dependency_completion_status_not_allowed"
    ):
        issue_dependency_completion_proof(reference, _policy())


def test_approved_not_applicable_reference_produces_bound_proof() -> None:
    content = {
        "kind": "dependency_result_reference",
        "check_id": RULE_ID,
        "dependency_rule_id": RULE_ID,
        "dependency_rule_version": RULE_VERSION,
        "dependency_rule_sha256": RULE_SHA256,
        "definition_manifest_sha256": "b" * 64,
        "case_id": CASE_ID,
        "input_snapshot_id": INPUT_SNAPSHOT_ID,
        "fact_set_snapshot_id": None,
        "source_run_id": "33333333-3333-4333-8333-333333333333",
        "result_id": "44444444-4444-4444-8444-444444444444",
        "task_result_sha256": "c" * 64,
        "assessment_sha256": "d" * 64,
        "evidence_manifest_sha256": "e" * 64,
        "observed_judgment": "not_applicable",
        "assessment_status": "not_applicable",
        "is_completion_proof": False,
    }
    reference = DependencyResultReference(
        **content,
        reference_sha256=sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )

    proof = issue_dependency_completion_proof(reference, _policy())

    assert proof.is_completion_proof is True
    assert proof.reference_sha256 == reference.reference_sha256
    assert proof.policy_sha256 == _policy().policy_sha256
