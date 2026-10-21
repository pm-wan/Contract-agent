import json
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from contract_app.rules.compiler import RuleValidationError
from contract_app.rules.skill_package import (
    SKILL_GENERATOR_VERSION,
    render_development_skill,
)
from contract_app.workflow.definitions import current_tool_schema
from contract_app.workflow.output_contract import candidate_output_schema

RULE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
)


def test_development_skill_package_is_deterministic_and_explicitly_unpublished() -> None:
    source = RULE_PATH.read_bytes()
    tool_contract = current_tool_schema()
    output_schema = candidate_output_schema()
    package = render_development_skill(
        source, tool_contract=tool_contract, output_schema=output_schema
    )
    replay = render_development_skill(
        source, tool_contract=tool_contract, output_schema=output_schema
    )

    assert package.skill_markdown == replay.skill_markdown
    assert package.skill_sha256 == replay.skill_sha256
    assert package.generated_at.tzinfo is not None
    assert package.rule_id == "legal.payment_conditions"
    assert package.rule_version == "1.0.0"
    assert package.rule_sha256 == sha256(source).hexdigest()
    assert (
        package.tool_contract_sha256
        == sha256(
            json.dumps(
                tool_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    )
    assert len(package.output_schema_sha256) == 64
    assert package.skill_sha256 == sha256(package.skill_markdown.encode("utf-8")).hexdigest()
    assert package.generator_version == SKILL_GENERATOR_VERSION
    assert package.status == "development_candidate"
    assert set(package.publication_blockers) == {
        "knowledge_domain",
        "basis_kind",
        "applicable_time_basis",
        "review_owner_role",
        "normative_refs_missing",
        "rule_not_published",
        "knowledge_source_content_unresolved",
        "approved_evaluation_missing",
        "publication_approval_missing",
        "rollback_target_missing",
        "expression_path_semantics_unresolved",
        "invalidation_dependency_unimplemented:document_version",
        "invalidation_dependency_unimplemented:parser_version",
        "invalidation_dependency_unimplemented:contract_total_amount",
        "invalidation_dependency_unimplemented:fact_set_snapshot",
        "invalidation_dependency_unimplemented:enterprise_position_version",
        "slot_invalidation_dependency_unimplemented:payment_clause_present:document_version",
        "slot_invalidation_dependency_unimplemented:payment_clause_present:parser_version",
        "slot_invalidation_dependency_unimplemented:payment_risk_judgment:document_version",
        "slot_invalidation_dependency_unimplemented:payment_risk_judgment:enterprise_position_version",
        "slot_invalidation_dependency_unimplemented:payment_risk_judgment:fact_set_snapshot",
        "slot_invalidation_dependency_unimplemented:payment_schedule:document_version",
        "slot_invalidation_dependency_unimplemented:payment_schedule:contract_total_amount",
        "slot_invalidation_dependency_unimplemented:payment_triggers:document_version",
        "slot_invalidation_dependency_unimplemented:prepayment_ratio:document_version",
        "slot_invalidation_dependency_unimplemented:prepayment_ratio:contract_total_amount",
        "slot_type_unimplemented:payment_clause_present:evidence",
        "slot_type_unimplemented:payment_risk_judgment:judgment",
        "direct_evidence_support_unimplemented",
        "human_review_condition_unimplemented:payment_schedule_conflicted",
        "human_review_condition_unimplemented:payment_total_not_reconcilable",
        "human_review_condition_unimplemented:enterprise_threshold_missing",
        "human_confirmation_evidence_unimplemented",
        "slot_validation_rule_unimplemented:derive_with_decimal_executor_only",
        "slot_validation_rule_unimplemented:judgment_must_follow_minimum_policy",
        "rule_tool_unimplemented:calculation.payment_ratio",
        "execution_budget_unimplemented:token_budget",
        "dependent_check_completion_proof_unimplemented:legal.price.definition",
    }
    assert "payment_clause_present" in package.skill_markdown
    assert "payment_risk_judgment" in package.skill_markdown
    assert "不得用于生产合同审查" in package.skill_markdown
    assert '"tool_contract"' in package.skill_markdown
    assert '"output_schema"' in package.skill_markdown
    assert '"direct_evidence_required": true' in package.skill_markdown
    assert "legal.payment_conditions.seed@1.0.0" in package.skill_markdown


def test_skill_contains_compiled_rule_governance_metadata() -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    expected = {
        "knowledge_domain": "legal",
        "basis_kind": "legal_validity_question",
        "applicable_time_basis": "signing_date",
        "review_owner_role": "legal_reviewer",
    }
    document.update(expected)
    source = yaml.safe_dump(document, allow_unicode=True).encode("utf-8")

    package = render_development_skill(
        source,
        tool_contract=current_tool_schema(),
        output_schema=candidate_output_schema(),
    )

    normalized_definition = json.loads(
        package.skill_markdown.split("```json\n", maxsplit=1)[1].split("\n```", maxsplit=1)[0]
    )
    assert {field: normalized_definition[field] for field in expected} == expected


def test_development_skill_renderer_rejects_published_rule_status() -> None:
    source = RULE_PATH.read_bytes().replace(
        b"status: approved_for_development", b"status: published"
    )

    with pytest.raises(RuleValidationError, match="rule_not_approved_for_development"):
        render_development_skill(
            source, tool_contract=current_tool_schema(), output_schema=candidate_output_schema()
        )


def test_development_skill_renderer_rejects_unallowlisted_tool_contract() -> None:
    tool_contract: dict[str, object] = {
        "type": "function",
        "function": {"name": "read_filesystem"},
    }

    with pytest.raises(ValueError, match="skill_tool_contract_not_allowlisted"):
        render_development_skill(
            RULE_PATH.read_bytes(),
            tool_contract=tool_contract,
            output_schema=candidate_output_schema(),
        )
