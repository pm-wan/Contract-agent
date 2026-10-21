import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from contract_app.documents.models import ParsedDocument
from contract_app.rules.compiler import (
    CompiledRiskRule,
    CompiledRule,
    RuleValidationError,
    load_development_rule,
    load_development_rule_bytes,
)
from contract_app.rules.runtime import RuleEvidence, RuleRun, SlotSubmission
from contract_app.workflow.dependency_completion_proof import (
    ApprovedCompletionPolicy,
    DependencyCompletionProof,
)

RULE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
)
FACTS = {
    "contract.party_role": "customer",
    "contract.contract_type": "service",
    "contract.jurisdiction": "CN-mainland",
}


def test_rule_compilation_fingerprint_is_for_the_exact_supplied_bytes() -> None:
    source = RULE_PATH.read_bytes()
    compiled = load_development_rule_bytes(source)
    assert compiled.source_sha256 == sha256(source).hexdigest()
    changed = source + b"\n# changed development artifact\n"
    assert load_development_rule_bytes(changed).source_sha256 == sha256(changed).hexdigest()
    assert load_development_rule(RULE_PATH).source_sha256 == compiled.source_sha256


def _dependencyless_rule() -> CompiledRule:
    definition = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    definition["dependencies"]["checks"] = []
    source = yaml.safe_dump(definition, allow_unicode=True, sort_keys=False).encode()
    return load_development_rule_bytes(source)


def _ready_run(
    text: str = "乙方提交成果并经甲方验收后支付。", warnings: tuple[str, ...] = ()
) -> tuple[RuleRun, UUID, RuleEvidence]:
    rule = _dependencyless_rule()
    plan = rule.plan(FACTS)
    document_id = uuid4()
    document = ParsedDocument(
        source_sha256=sha256(text.encode()).hexdigest(),
        media_type="application/pdf",
        parser_version="fixture-1",
        pages=(text,),
        blocks=(),
        warnings=warnings,
    )
    span = RuleEvidence(
        kind="document_span",
        document_version_id=document_id,
        source_sha256=document.source_sha256,
        page=1,
        start_offset=0,
        end_offset=len(text),
        quote=text,
        quote_sha256=sha256(text.encode()).hexdigest(),
    )
    return RuleRun(rule, plan, {document_id: document}, facts=FACTS), document_id, span


@pytest.mark.parametrize("coordinate", ("page", "start_offset", "end_offset"))
def test_rule_evidence_rejects_boolean_document_coordinates(coordinate: str) -> None:
    with pytest.raises(RuleValidationError, match="evidence_coordinate_invalid"):
        RuleEvidence(kind="document_span", **{coordinate: True})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field_name", ("source_sha256", "quote", "quote_sha256", "parser_coverage")
)
def test_rule_evidence_rejects_non_string_identity_fields(field_name: str) -> None:
    with pytest.raises(RuleValidationError, match="evidence_identity_invalid"):
        RuleEvidence(kind="document_span", **{field_name: 1})  # type: ignore[arg-type]


def test_required_slots_cannot_be_skipped_and_complete_candidates_do_not_auto_pass() -> None:
    run, _, span = _ready_run()
    assert run.assess().status == "incomplete"
    with pytest.raises(RuleValidationError, match="slot_dependency_unresolved"):
        run.submit(
            SlotSubmission(
                "payment_schedule",
                "filled",
                [{"trigger": "验收", "amount_type": "percentage", "percentage": "100"}],
                (span,),
            )
        )
    run.submit(SlotSubmission("payment_clause_present", "filled", True, (span,)))
    assert "payment_risk_judgment" in run.assess().missing_slots
    run.submit(
        SlotSubmission(
            "payment_schedule",
            "filled",
            [{"trigger": "验收", "amount_type": "percentage", "percentage": "100"}],
            (span,),
            "contract.payment_schedule",
        )
    )
    run.submit(
        SlotSubmission(
            "payment_triggers",
            "filled",
            [{"stage": "尾款", "conditions": ["验收合格"], "acceptance_required": True}],
            (span,),
            "contract.payment_trigger",
        )
    )
    run.submit(SlotSubmission("prepayment_ratio", "human_review"))
    with pytest.raises(
        RuleValidationError,
        match="slot_validation_rule_unimplemented:judgment_must_follow_minimum_policy",
    ):
        run.submit(
            SlotSubmission(
                "payment_risk_judgment",
                "filled",
                {"outcome": "conform", "reasons": ["候选事实显示验收后付款"]},
                (span,),
            )
        )
    run.submit(SlotSubmission("payment_risk_judgment", "human_review"))
    assessment = run.assess()
    assert assessment.status == "human_review"
    assert "enterprise_position_not_approved" in assessment.reasons
    assert assessment.unresolved_minimum_judgment_indexes == (1,)
    assert "minimum_judgment_condition_unknown" in assessment.reasons
    assert assessment.payment_totals is not None
    assert assessment.payment_totals.percentage_value_sum == "100"
    assert assessment.payment_totals.fixed_amount_totals == ()


def test_absence_with_complete_scope_routes_to_needs_material() -> None:
    run, document_id, span = _ready_run("本合同没有可定位的付款安排。")
    absence = RuleEvidence(
        kind="absence_evidence",
        document_version_id=document_id,
        source_sha256=span.source_sha256,
        searched_sections=("全部正文",),
        query_terms=("支付节点", "价款结算"),
        parser_coverage="complete",
    )
    run.submit(SlotSubmission("payment_clause_present", "filled", False, (absence,)))
    run.submit(SlotSubmission("payment_risk_judgment", "human_review"))
    assessment = run.assess()
    assert assessment.status == "needs_material"
    assert [item.judgment for item in assessment.matched_minimum_policy_rules] == ["needs_material"]
    assert (
        assessment.matched_minimum_policy_rules[0].reason
        == "未定位到付款约定且缺少充分 absence evidence 时不得通过"
    )
    with pytest.raises(RuleValidationError, match="slot_not_required"):
        run.submit(SlotSubmission("payment_schedule", "needs_material"))


def test_bad_quote_and_schema_are_rejected_before_slot_acceptance() -> None:
    run, _, span = _ready_run()
    with pytest.raises(RuleValidationError, match="document_quote_hash_mismatch"):
        run.submit(
            SlotSubmission(
                "payment_clause_present",
                "filled",
                True,
                (replace(span, quote_sha256="0" * 64),),
            )
        )


def test_quote_hash_is_required_only_when_the_compiled_policy_requires_it() -> None:
    run, _, span = _ready_run()
    with pytest.raises(RuleValidationError, match="document_span_incomplete"):
        run.validate_candidate_submission(
            SlotSubmission(
                "payment_clause_present",
                "filled",
                True,
                (replace(span, quote_sha256=None),),
            )
        )
    rule = replace(
        run.rule,
        evidence_policy=replace(run.rule.evidence_policy, quote_hash_required=False),
    )
    policy_run = RuleRun(rule, run.plan, run.documents, facts=FACTS)
    policy_run.validate_candidate_submission(
        SlotSubmission(
            "payment_clause_present",
            "filled",
            True,
            (replace(span, quote_sha256=None),),
        )
    )
    with pytest.raises(RuleValidationError, match="document_quote_mismatch"):
        run.submit(
            SlotSubmission(
                "payment_clause_present",
                "filled",
                True,
                (replace(span, start_offset=1),),
            )
        )
    run.submit(SlotSubmission("payment_clause_present", "filled", True, (span,)))
    with pytest.raises(RuleValidationError, match="slot_value_schema_invalid"):
        run.submit(
            SlotSubmission(
                "payment_schedule",
                "filled",
                [{"trigger": "签约", "amount_type": "percentage", "percentage": 0.3}],
                (span,),
                "contract.payment_schedule",
            )
        )


def test_candidate_validation_is_read_only_when_rule_applicability_is_unknown() -> None:
    ready_run, _, span = _ready_run()
    unknown_run = RuleRun(
        ready_run.rule,
        ready_run.rule.plan({}, frozenset()),
        ready_run.documents,
    )
    assert unknown_run.plan.applicability == "undetermined"
    unknown_run.validate_candidate_submission(
        SlotSubmission("payment_clause_present", "filled", True, (span,))
    )
    assert unknown_run.assess().status == "undetermined"
    with pytest.raises(RuleValidationError, match="rule_plan_not_ready"):
        unknown_run.submit(SlotSubmission("payment_clause_present", "filled", True, (span,)))


def test_wrong_typed_fact_cannot_make_required_slot_optional(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["dependencies"]["facts"].append("contract.payment_clause_present")
    document["slots"][0]["required_when"] = {"eq": ["contract.payment_clause_present", True]}
    source = yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode()
    rule_path = tmp_path / "strict-json-rule.yaml"
    rule_path.write_bytes(source)
    rule = load_development_rule(rule_path)
    facts = {**FACTS, "contract.payment_clause_present": 1}
    rule = replace(rule, dependent_checks=())
    plan = rule.plan(facts)
    run = RuleRun(rule, plan, {}, facts=facts)

    assert plan.applicability == "applicable"
    assert run.assess().status == "incomplete"
    assert "payment_clause_present" in run.assess().missing_slots


def test_filled_fact_candidates_require_a_declared_fact_type() -> None:
    run, _, span = _ready_run()
    value = [{"trigger": "验收", "amount_type": "percentage", "percentage": "100"}]

    with pytest.raises(RuleValidationError, match="slot_fact_type_required"):
        run.validate_candidate_submission(
            SlotSubmission("payment_schedule", "filled", value, (span,))
        )
    with pytest.raises(RuleValidationError, match="slot_fact_type_not_accepted"):
        run.validate_candidate_submission(
            SlotSubmission("payment_schedule", "filled", value, (span,), "contract.payment_trigger")
        )
    run.validate_candidate_submission(
        SlotSubmission(
            "payment_schedule",
            "filled",
            value,
            (span,),
            "contract.payment_schedule",
        )
    )
    with pytest.raises(RuleValidationError, match="nonfilled_slot_fact_type_invalid"):
        run.validate_candidate_submission(
            SlotSubmission(
                "payment_schedule",
                "human_review",
                evidence=(span,),
                fact_type="contract.payment_schedule",
            )
        )
    with pytest.raises(RuleValidationError, match="slot_fact_type_not_allowed"):
        run.validate_candidate_submission(
            SlotSubmission(
                "payment_clause_present",
                "filled",
                True,
                (span,),
                "contract.payment_schedule",
            )
        )


def test_submitted_slot_candidates_snapshot_nested_values_and_evidence() -> None:
    run, _, span = _ready_run()
    run.submit(SlotSubmission("payment_clause_present", "filled", True, [span]))
    mutable_value = [{"trigger": "验收", "amount_type": "percentage", "percentage": "30"}]
    mutable_evidence = [span]
    schedule = SlotSubmission(
        "payment_schedule",
        "filled",
        mutable_value,
        mutable_evidence,
        "contract.payment_schedule",
    )
    run.submit(schedule)

    mutable_value[0]["percentage"] = "95"
    mutable_evidence.clear()

    totals = run.assess().payment_totals
    assert totals is not None
    assert totals.percentage_value_sum == "30"
    assert len(run._submissions["payment_schedule"].evidence) == 1
    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        schedule.value[0]["percentage"] = "80"


def test_rule_evidence_snapshots_mutable_scope_sequences() -> None:
    sections = ["付款条件"]
    query_terms = ["验收后付款"]
    quote_hashes = ["a" * 64]
    absence = RuleEvidence(
        kind="absence_evidence",
        searched_sections=sections,
        query_terms=query_terms,
        parser_coverage="complete",
    )
    calculation = RuleEvidence(
        kind="calculation",
        supporting_quote_hashes=quote_hashes,
    )

    sections.clear()
    query_terms.append("预付款")
    quote_hashes.clear()

    assert absence.searched_sections == ("付款条件",)
    assert absence.query_terms == ("验收后付款",)
    assert calculation.supporting_quote_hashes == ("a" * 64,)


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("document_span", {"searched_sections": ("付款条件",)}),
        ("absence_evidence", {"page": 1}),
        ("calculation", {"quote": "付款约定"}),
        ("human_confirmation", {"source_sha256": "a" * 64}),
    ],
)
def test_rule_evidence_rejects_fields_outside_its_kind(
    kind: str, fields: dict[str, object]
) -> None:
    with pytest.raises(RuleValidationError, match="evidence_field_not_allowed_for_kind"):
        RuleEvidence(kind=kind, **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [{1: "non-json object key"}, float("nan"), {"value": object()}])
def test_slot_submission_rejects_values_outside_json_domain(value: object) -> None:
    with pytest.raises(RuleValidationError, match="slot_value_not_json_compatible"):
        SlotSubmission("payment_schedule", "filled", value)


def test_rule_run_requires_the_fact_set_bound_to_its_plan() -> None:
    rule = load_development_rule(RULE_PATH)
    plan = rule.plan(FACTS, frozenset({"legal.price.definition"}))

    with pytest.raises(RuleValidationError, match="plan_facts_mismatch"):
        RuleRun(
            rule,
            plan,
            {},
            facts={
                "contract.party_role": "customer",
                "contract.contract_type": "unknown",
                "contract.jurisdiction": "CN-mainland",
            },
        )

    with pytest.raises(RuleValidationError, match="plan_fingerprint_mismatch"):
        RuleRun(rule, replace(plan, completed_checks=()), {}, facts=FACTS)


def test_rule_run_rejects_declared_checks_without_completion_proofs() -> None:
    rule = load_development_rule(RULE_PATH)
    plan = rule.plan(FACTS, frozenset({"legal.price.definition"}))

    with pytest.raises(RuleValidationError, match="dependency_completion_proof_unimplemented"):
        RuleRun(rule, plan, {}, facts=FACTS)


def test_rule_run_accepts_only_a_matching_completion_proof() -> None:
    rule = load_development_rule(RULE_PATH)
    check_id = "legal.price.definition"
    plan = rule.plan(FACTS, frozenset({check_id}))
    policy_content = {
        "kind": "approved_dependency_completion_policy",
        "policy_id": "qa.dependency-completion",
        "policy_version": "1.0.0",
        "allowed_assessment_statuses": ["not_applicable"],
        "allowed_judgments": ["not_applicable"],
        "approval_record_sha256": "e" * 64,
        "approver_role": "qa-approver",
    }
    policy_hash = sha256(
        json.dumps(
            policy_content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    policy = ApprovedCompletionPolicy(**policy_content, policy_sha256=policy_hash)
    proof_content = {
        "kind": "dependency_completion_proof",
        "check_id": check_id,
        "reference_sha256": "a" * 64,
        "policy_id": "qa.dependency-completion",
        "policy_version": "1.0.0",
        "policy_sha256": policy.policy_sha256,
        "policy": policy.model_dump(mode="json"),
        "source_run_id": str(uuid4()),
        "result_id": str(uuid4()),
        "observed_judgment": "not_applicable",
        "assessment_status": "not_applicable",
        "is_completion_proof": True,
    }
    proof_hash = sha256(
        json.dumps(
            proof_content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    proof = DependencyCompletionProof(**proof_content, proof_sha256=proof_hash)

    run = RuleRun(rule, plan, {}, facts=FACTS, completion_proofs={check_id: proof})

    assert run.assess().status == "incomplete"


def test_rule_run_accepts_trusted_slots_while_dependency_remains_blocked() -> None:
    rule = load_development_rule(RULE_PATH)
    plan = rule.plan(FACTS)
    document_id = uuid4()
    text = "付款约定：验收后支付。"
    document = ParsedDocument(
        source_sha256=sha256(text.encode()).hexdigest(),
        media_type="application/pdf",
        parser_version="fixture-1",
        pages=(text,),
        blocks=(),
        warnings=(),
    )
    evidence = RuleEvidence(
        kind="document_span",
        document_version_id=document_id,
        source_sha256=document.source_sha256,
        page=1,
        start_offset=0,
        end_offset=len(text),
        quote=text,
        quote_sha256=sha256(text.encode()).hexdigest(),
    )
    run = RuleRun(rule, plan, {document_id: document}, facts=FACTS)

    run.submit(
        SlotSubmission(
            "payment_clause_present",
            "filled",
            True,
            (evidence,),
        )
    )

    assessment = run.assess()
    assert assessment.status == "blocked"
    assert assessment.reasons == ("legal.price.definition",)


def test_rule_run_rejects_tampered_derived_plan_fields() -> None:
    rule = load_development_rule(RULE_PATH)
    plan = rule.plan(FACTS)
    assert plan.blocked_by_checks

    tampered_plans = (
        replace(plan, blocked_by_checks=()),
        replace(plan, applicability="not_applicable"),
        replace(plan, potential_required_slots=()),
        replace(plan, publication_blockers=()),
    )
    for tampered_plan in tampered_plans:
        with pytest.raises(RuleValidationError, match="plan_content_mismatch"):
            RuleRun(rule, tampered_plan, {}, facts=FACTS)


def test_rule_run_freezes_its_definition_and_input_mapping() -> None:
    rule = _dependencyless_rule()
    plan = rule.plan(FACTS)
    document_id = uuid4()
    document = ParsedDocument(
        source_sha256="a" * 64,
        media_type="application/pdf",
        parser_version="fixture-1",
        pages=("付款约定",),
        blocks=(),
        warnings=(),
    )
    documents = {document_id: document}
    run = RuleRun(rule, plan, documents, facts=FACTS)

    documents.clear()
    assert run.documents[document_id] is document
    with pytest.raises(TypeError):
        run.documents[document_id] = document  # type: ignore[index]
    with pytest.raises(AttributeError):
        run.rule = rule  # type: ignore[misc]
    with pytest.raises(AttributeError):
        run.plan = plan  # type: ignore[misc]
    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        run._facts["contract.party_role"] = "supplier"  # type: ignore[index]


def test_rule_plan_rejects_mutable_or_malformed_sequence_fields() -> None:
    rule = load_development_rule(RULE_PATH)
    plan = rule.plan(FACTS)

    with pytest.raises(RuleValidationError, match="rule_plan_structure_invalid"):
        replace(plan, blocked_by_checks=list(plan.blocked_by_checks))  # type: ignore[arg-type]


def test_minimum_judgment_rules_can_evaluate_declared_contract_facts() -> None:
    source_rule = load_development_rule(RULE_PATH)
    fact_rule = replace(
        source_rule,
        dependent_checks=(),
        slots=(),
        minimum_judgment_rules=(
            CompiledRiskRule(
                when={"eq": ["contract.party_role", "customer"]},
                judgment="issue_found",
                reason="synthetic declared-fact rule",
            ),
        ),
    )
    plan = fact_rule.plan(FACTS)
    run = RuleRun(fact_rule, plan, {}, facts=FACTS)

    assessment = run.assess()

    assert assessment.status == "human_review"
    assert [rule.reason for rule in assessment.matched_minimum_policy_rules] == [
        "synthetic declared-fact rule"
    ]
    assert assessment.unresolved_minimum_judgment_indexes == ()


def test_schedule_validation_requires_percentages_and_fixed_amount_currency() -> None:
    run, _, span = _ready_run()
    run.submit(SlotSubmission("payment_clause_present", "filled", True, (span,)))

    with pytest.raises(RuleValidationError, match="percentage_must_be_decimal_string"):
        run.submit(
            SlotSubmission(
                "payment_schedule",
                "filled",
                [{"trigger": "签约", "amount_type": "percentage"}],
                (span,),
                "contract.payment_schedule",
            )
        )
    with pytest.raises(RuleValidationError, match="fixed_payment_amount_requires_currency"):
        run.submit(
            SlotSubmission(
                "payment_schedule",
                "filled",
                [{"trigger": "签约", "amount_type": "fixed", "amount": "100"}],
                (span,),
                "contract.payment_schedule",
            )
        )


def test_prepayment_ratio_remains_blocked_without_a_trusted_decimal_executor() -> None:
    run, _, span = _ready_run()
    run.submit(SlotSubmission("payment_clause_present", "filled", True, (span,)))
    run.submit(
        SlotSubmission(
            "payment_schedule",
            "filled",
            [{"trigger": "签约", "amount_type": "percentage", "percentage": "20"}],
            (span,),
            "contract.payment_schedule",
        )
    )
    run.submit(
        SlotSubmission(
            "payment_triggers",
            "filled",
            [{"stage": "签约", "conditions": ["签约"]}],
            (span,),
            "contract.payment_trigger",
        )
    )

    calculation = RuleEvidence(
        kind="calculation", supporting_quote_hashes=(span.quote_sha256 or "",)
    )
    with pytest.raises(
        RuleValidationError,
        match="slot_validation_rule_unimplemented:derive_with_decimal_executor_only",
    ):
        run.submit(
            SlotSubmission(
                "prepayment_ratio",
                "filled",
                "0.2",
                (calculation,),
                "contract.prepayment_ratio",
            )
        )


def test_absence_on_incomplete_parse_is_not_accepted() -> None:
    run, document_id, span = _ready_run("", ("page_1_has_no_text_layer",))
    absence = RuleEvidence(
        kind="absence_evidence",
        document_version_id=document_id,
        source_sha256=span.source_sha256,
        searched_sections=("全部正文",),
        query_terms=("付款",),
        parser_coverage="complete",
    )
    with pytest.raises(RuleValidationError, match="absence_source_not_complete"):
        run.submit(SlotSubmission("payment_clause_present", "filled", False, (absence,)))


def test_plan_binding_and_calculation_provenance_fail_closed() -> None:
    run, _, span = _ready_run()
    with pytest.raises(RuleValidationError, match="plan_rule_version_mismatch"):
        RuleRun(run.rule, replace(run.plan, rule_sha256="0" * 64), run.documents)
    run.submit(SlotSubmission("payment_clause_present", "filled", True, (span,)))
    run.submit(
        SlotSubmission(
            "payment_schedule",
            "filled",
            [{"trigger": "验收", "amount_type": "residual"}],
            (span,),
            "contract.payment_schedule",
        )
    )
    run.submit(
        SlotSubmission(
            "payment_triggers",
            "filled",
            [{"stage": "尾款", "conditions": ["验收合格"]}],
            (span,),
            "contract.payment_trigger",
        )
    )
    with pytest.raises(RuleValidationError, match="calculation_provenance_invalid"):
        run.submit(
            SlotSubmission(
                "prepayment_ratio",
                "filled",
                "0",
                (RuleEvidence(kind="calculation", supporting_quote_hashes=("0" * 64,)),),
                "contract.prepayment_ratio",
            )
        )
