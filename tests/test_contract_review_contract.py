from pathlib import Path
from uuid import uuid4

import pytest

from contract_app.rules.compiler import load_development_rule
from contract_app.rules.runtime import RuleRun
from contract_app.workflow.contract_review import (
    _fallbacks_for_unreturned_slots,
    _rule_plan_limitations,
)
from contract_app.workflow.rule_plan_execution import RulePlanExecutionSnapshot

RULE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
)


def test_worker_keeps_every_omitted_rule_slot_as_an_unresolved_candidate() -> None:
    rule = load_development_rule(RULE_PATH)
    returned_slot_ids = {"payment_clause_present"}

    fallbacks, limitations = _fallbacks_for_unreturned_slots(rule, returned_slot_ids)

    expected_slot_ids = {slot.slot_id for slot in rule.slots}
    fallback_by_id = {candidate["slot_id"]: candidate for candidate in fallbacks}
    assert returned_slot_ids.isdisjoint(fallback_by_id)
    assert returned_slot_ids | set(fallback_by_id) == expected_slot_ids
    assert set(fallback_by_id) == {
        slot.slot_id for slot in rule.slots if slot.slot_id not in returned_slot_ids
    }
    assert all(candidate["value"] is None for candidate in fallbacks)
    assert all(candidate["fact_type"] is None for candidate in fallbacks)
    assert all(candidate["evidence_ids"] == [] for candidate in fallbacks)
    assert fallback_by_id["payment_schedule"]["state"] == "human_review"
    assert fallback_by_id["payment_triggers"]["state"] == "undetermined"
    assert set(limitations) == {
        f"模型未返回槽位 {slot_id} 的候选值。" for slot_id in fallback_by_id
    }


def test_worker_does_not_add_fallbacks_for_returned_rule_slots() -> None:
    rule = load_development_rule(RULE_PATH)
    returned_slot_ids = {slot.slot_id for slot in rule.slots}

    fallbacks, limitations = _fallbacks_for_unreturned_slots(rule, returned_slot_ids)

    assert fallbacks == ()
    assert limitations == ()


def test_worker_explains_unknown_applicability_and_frozen_rule_blockers() -> None:
    rule = load_development_rule(RULE_PATH)
    plan = rule.plan({}, frozenset())
    assessment = RuleRun(rule, plan, {}).assess()

    notes = _rule_plan_limitations(plan, assessment)

    assert assessment.status == "undetermined"
    assert any("适用性尚未确定" in note for note in notes)
    assert any("legal.price.definition" in note for note in notes)
    assert any(f"{len(plan.publication_blockers)} 项发布阻断" in note for note in notes)
    assert any("不据此生成正式风险结论" in note for note in notes)


def test_rule_plan_execution_snapshot_binds_run_facts_and_rule_identity() -> None:
    rule = load_development_rule(RULE_PATH)
    plan = rule.plan({})
    assessment = RuleRun(rule, plan, {}).assess()
    input_snapshot_id = uuid4()
    fact_set_snapshot_id = uuid4()

    snapshot = RulePlanExecutionSnapshot.capture(
        input_snapshot_id=input_snapshot_id,
        fact_set_snapshot_id=fact_set_snapshot_id,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        rule_source_sha256=rule.source_sha256,
        plan_sha256=plan.plan_sha256,
        facts_sha256=plan.facts_sha256,
        assessment_status=assessment.status,
        blocked_by_checks=plan.blocked_by_checks,
        missing_slots=assessment.missing_slots,
        unresolved_minimum_judgment_indexes=assessment.unresolved_minimum_judgment_indexes,
    )

    restored = RulePlanExecutionSnapshot.model_validate(snapshot.model_dump(mode="json"))
    assert restored == snapshot
    assert restored.input_snapshot_id == input_snapshot_id
    assert restored.fact_set_snapshot_id == fact_set_snapshot_id
    assert restored.rule_source_sha256 == rule.source_sha256
    assert restored.blocked_by_checks == ("legal.price.definition",)
    assert restored.is_dependency_completion_proof is False


def test_rule_plan_execution_snapshot_rejects_tampering() -> None:
    rule = load_development_rule(RULE_PATH)
    plan = rule.plan({})
    assessment = RuleRun(rule, plan, {}).assess()
    snapshot = RulePlanExecutionSnapshot.capture(
        input_snapshot_id=uuid4(),
        fact_set_snapshot_id=None,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        rule_source_sha256=rule.source_sha256,
        plan_sha256=plan.plan_sha256,
        facts_sha256=plan.facts_sha256,
        assessment_status=assessment.status,
        blocked_by_checks=plan.blocked_by_checks,
        missing_slots=assessment.missing_slots,
        unresolved_minimum_judgment_indexes=assessment.unresolved_minimum_judgment_indexes,
    )
    changed = snapshot.model_dump(mode="json")
    changed["blocked_by_checks"] = []

    with pytest.raises(ValueError, match="rule_plan_assessment_hash_mismatch"):
        RulePlanExecutionSnapshot.model_validate(changed)
