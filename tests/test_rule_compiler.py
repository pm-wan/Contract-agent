import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from contract_app import bounded_files
from contract_app.rules.compiler import (
    MAX_DEVELOPMENT_RULE_BYTES,
    RuleValidationError,
    _validate_yaml_source_structure,
    canonical_rule_facts,
    evaluate_expression,
    load_development_rule,
    load_development_rule_bytes,
)
from contract_app.workflow.execution_budget import resolve_review_execution_budget

RULE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
)


def test_rule_path_reads_only_one_byte_past_source_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "large-rule.yaml"
    path.write_bytes(b"placeholder")
    read_sizes: list[int] = []

    class OversizedRuleFile:
        def __init__(self, descriptor: int) -> None:
            self.descriptor = descriptor

        def __enter__(self) -> Any:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
            os.close(self.descriptor)
            return False

        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            return b"x" * size

    def fdopen_spy(descriptor: int, mode: str = "r") -> OversizedRuleFile:
        assert mode == "rb"
        return OversizedRuleFile(descriptor)

    monkeypatch.setattr(bounded_files.os, "fdopen", fdopen_spy)

    with pytest.raises(RuleValidationError, match="rule_file_too_large"):
        load_development_rule(path)

    assert read_sizes == [MAX_DEVELOPMENT_RULE_BYTES + 1]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files are unavailable")
def test_rule_path_rejects_fifo_instead_of_waiting_for_a_writer(tmp_path: Path) -> None:
    path = tmp_path / "rule.pipe"
    os.mkfifo(path)

    with pytest.raises(OSError):
        load_development_rule(path)


def test_rule_rejects_yaml_nesting_before_python_recursion_limit() -> None:
    source = RULE_PATH.read_text(encoding="utf-8")
    expression_start = source.index("  expression:\n")
    dependencies_start = source.index("dependencies:\n", expression_start)
    expression = "{eq: [contract.party_role, customer]}"
    for _ in range(70):
        expression = f"{{all: [{expression}]}}"
    nested_source = (
        source[:expression_start] + f"  expression: {expression}\n" + source[dependencies_start:]
    ).encode()

    with pytest.raises(RuleValidationError, match="rule_yaml_invalid"):
        load_development_rule_bytes(nested_source)


def test_rule_rejects_recursive_yaml_alias() -> None:
    source = RULE_PATH.read_text(encoding="utf-8")
    expression_start = source.index("  expression:\n")
    dependencies_start = source.index("dependencies:\n", expression_start)
    recursive_source = (
        source[:expression_start]
        + "  expression: &recursive [*recursive]\n"
        + source[dependencies_start:]
    ).encode()

    with pytest.raises(RuleValidationError, match="rule_yaml_invalid"):
        load_development_rule_bytes(recursive_source)


def test_yaml_source_allows_nonrecursive_aliases() -> None:
    _validate_yaml_source_structure(b"first: &values [one, two]\nsecond: *values\n")


def test_yaml_source_event_count_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contract_app.rules.compiler.MAX_YAML_PARSE_EVENTS", 5)

    with pytest.raises(yaml.YAMLError):
        _validate_yaml_source_structure(b"values: [one, two]\n")


def _modified_rule(tmp_path: Path, change: str, value: Any) -> Path:
    data = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    if change == "tool":
        data["execution"]["tool_allowlist"].append(value)
    elif change == "slot_tool":
        data["slots"][0]["allowed_tools"].append(value)
    elif change == "cycle":
        data["slots"][0]["dependencies"] = ["payment_risk_judgment"]
    elif change == "missing_slot_dependency":
        data["slots"][0]["dependencies"] = ["payment_clause_presnt"]
    elif change == "schema":
        data["slots"][0]["value_schema"]["type"] = value
    elif change == "expression":
        data["applicability"]["expression"] = value
    elif change == "status":
        data["status"] = value
    elif change == "unknown":
        data[value] = True
    elif change == "reference":
        data["enterprise_position_refs"] = value
    elif change == "legal_reference":
        data["normative_refs"] = value
    elif change == "unknown_applicability":
        data["applicability"]["expression_typo"] = True
    elif change == "unknown_execution":
        data["execution"]["tool_allowlist_typo"] = []
    elif change == "unknown_risk_rule":
        data["risk_policy"]["minimum_judgment_rules"][0]["judgement"] = "issue_found"
    elif change == "unknown_slot_evidence":
        data["slots"][0]["evidence_policy"]["quote_required_typo"] = True
    elif change == "unknown_slot_validation_rule":
        data["slots"][0]["validation_rules"].append("ignore_all_failures")
    elif change == "unsupported_step":
        data["execution"]["deterministic_steps"].append("skip_required_slots")
    elif change == "invalid_risk_outcome_type":
        data["risk_policy"]["minimum_judgment_rules"][0]["judgment"] = []
    elif change == "invalid_risk_severity_type":
        data["risk_policy"]["minimum_judgment_rules"][1]["severity"] = []
    elif change == "invalid_slot_type":
        data["slots"][0]["slot_type"] = []
    elif change == "undeclared_applicability_reference":
        data["applicability"]["expression"] = {"eq": ["contract.typo", "customer"]}
    elif change == "undeclared_slot_condition_reference":
        data["slots"][1]["required_when"] = {"eq": ["payment_cluase_present", True]}
    elif change == "undeclared_risk_reference":
        data["risk_policy"]["minimum_judgment_rules"][0]["when"] = {
            "eq": ["payment_cluase_present", False]
        }
    elif change == "fact_slot_identifier_collision":
        data["dependencies"]["facts"].append("payment_clause_present")
    path = tmp_path / "rule.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_payment_rule_plan_is_deterministic_and_keeps_all_potential_slots() -> None:
    rule = load_development_rule(RULE_PATH)
    assert rule.eval_suite_refs == ("legal.payment_conditions.seed@1.0.0",)
    facts = {
        "contract.party_role": "customer",
        "contract.contract_type": "service",
        "contract.jurisdiction": "CN-mainland",
    }
    same_facts_other_order = {
        "contract.contract_type": "service",
        "contract.party_role": "customer",
        "contract.jurisdiction": "CN-mainland",
    }
    plan = rule.plan(facts)
    assert plan == rule.plan(same_facts_other_order)
    assert plan.applicability == "applicable"
    assert plan.blocked_by_checks == ("legal.price.definition",)
    assert plan.potential_required_slots == (
        "payment_clause_present",
        "payment_schedule",
        "payment_triggers",
        "prepayment_ratio",
        "payment_risk_judgment",
    )
    assert rule.invalidation_dependencies == (
        "document_version",
        "parser_version",
        "contract_total_amount",
        "fact_set_snapshot",
        "enterprise_position_version",
    )
    assert rule.evidence_policy.direct_evidence_required is True
    assert rule.evidence_policy.quote_hash_required is True
    assert rule.evidence_policy.absence_required_fields == (
        "searched_sections",
        "parser_coverage",
        "query_terms",
    )
    slot_dependencies = {slot.slot_id: slot.invalidation_dependencies for slot in rule.slots}
    assert slot_dependencies["payment_schedule"] == (
        "document_version",
        "contract_total_amount",
    )
    accepted_fact_types = {slot.slot_id: slot.accepted_fact_types for slot in rule.slots}
    assert accepted_fact_types["payment_schedule"] == ("contract.payment_schedule",)
    assert accepted_fact_types["payment_triggers"] == ("contract.payment_trigger",)
    assert accepted_fact_types["prepayment_ratio"] == ("contract.prepayment_ratio",)
    slot_types = {slot.slot_id: slot.slot_type for slot in rule.slots}
    assert slot_types == {
        "payment_clause_present": "evidence",
        "payment_schedule": "fact",
        "payment_triggers": "fact",
        "prepayment_ratio": "fact",
        "payment_risk_judgment": "judgment",
    }
    assert set(plan.publication_blockers) == {
        "knowledge_domain",
        "basis_kind",
        "applicable_time_basis",
        "review_owner_role",
        "normative_refs_missing",
        "dependent_check_completion_proof_unimplemented:legal.price.definition",
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
    }
    assert rule.normative_refs == ()
    assert rule.enterprise_position_refs == ("enterprise.payment-policy/mvp-v1",)
    assert rule.plan(facts, frozenset({"legal.price.definition"})).blocked_by_checks == ()
    assert (
        "dependent_check_completion_proof_unimplemented:legal.price.definition"
        in rule.plan(facts, frozenset({"legal.price.definition"})).publication_blockers
    )
    assert (
        rule.plan(
            {
                "contract.party_role": "supplier",
                "contract.contract_type": "service",
                "contract.jurisdiction": "CN-mainland",
            }
        ).applicability
        == "not_applicable"
    )
    assert (
        rule.plan(
            {
                "contract.party_role": "customer",
                "contract.contract_type": "service",
                "contract.jurisdiction": "US-California",
            }
        ).applicability
        == "not_applicable"
    )
    assert (
        rule.plan(
            {"contract.party_role": "customer", "contract.contract_type": "service"}
        ).applicability
        == "undetermined"
    )
    assert rule.plan({"contract.party_role": "customer"}).applicability == "undetermined"
    assert (
        rule.plan(
            {
                "contract.party_role": "customer",
                "contract.contract_type": 1,
                "contract.jurisdiction": "CN-mainland",
            }
        ).applicability
        == "undetermined"
    )
    assert plan.plan_sha256 != rule.plan({"contract.party_role": "customer"}).plan_sha256
    with pytest.raises(RuleValidationError, match="rule_plan_completed_check_unknown"):
        rule.plan(facts, frozenset({"legal.price.definition", "legal.unlisted.check"}))
    with pytest.raises(RuleValidationError, match="rule_plan_completed_checks_invalid"):
        rule.plan(facts, frozenset({1}))  # type: ignore[arg-type]
    with pytest.raises(RuleValidationError, match="rule_plan_completed_checks_invalid"):
        rule.plan(facts, {"legal.price.definition"})  # type: ignore[arg-type]


def test_rule_rejects_fact_names_that_collide_with_slot_ids(tmp_path: Path) -> None:
    path = _modified_rule(tmp_path, "fact_slot_identifier_collision", None)

    with pytest.raises(RuleValidationError, match="fact_slot_identifier_collision"):
        load_development_rule(path)


def test_rule_rejects_unstable_evaluation_suite_path_references(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["eval_suite_refs"] = ["eval/legal.payment_conditions.cases.yaml"]
    path = tmp_path / "path-based-evaluation-reference.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RuleValidationError, match="evaluation_suite_reference_invalid"):
        load_development_rule(path)


def test_rule_rejects_malformed_invalidation_dependency_identifier(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["invalidation_dependencies"] = ["Document Version"]
    path = tmp_path / "invalid-invalidation-reference.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RuleValidationError, match="invalidation_dependencies_identifier_invalid"):
        load_development_rule(path)


def test_rule_rejects_malformed_accepted_fact_type_identifier(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["slots"][1]["accepted_fact_types"] = ["Payment Schedule"]
    path = tmp_path / "invalid-fact-type.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RuleValidationError, match="accepted_fact_type_identifier_invalid"):
        load_development_rule(path)


def test_fact_slot_requires_a_declared_type_for_publication(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["slots"][1]["accepted_fact_types"] = []
    path = tmp_path / "fact-type-not-declared.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert "slot_fact_type_missing:payment_schedule" in rule.publication_blockers


def test_accepted_fact_types_are_only_valid_for_fact_slots(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["slots"][0]["accepted_fact_types"] = ["contract.payment_clause"]
    path = tmp_path / "accepted-fact-type-on-evidence-slot.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RuleValidationError, match="accepted_fact_types_require_fact_slot"):
        load_development_rule(path)


def test_absence_evidence_policy_cannot_omit_required_scope_dimensions(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["evidence_policy"]["absence_evidence"]["required_fields"].remove("parser_coverage")
    path = tmp_path / "incomplete-absence-evidence-policy.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(
        RuleValidationError,
        match="absence_required_fields_incomplete:parser_coverage",
    ):
        load_development_rule(path)


def test_quote_required_false_is_preserved_and_blocks_unimplemented_mode(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["slots"][0]["evidence_policy"]["quote_required"] = False
    path = tmp_path / "quote-not-required.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)
    slot = next(slot for slot in rule.slots if slot.slot_id == "payment_clause_present")

    assert slot.quote_required is False
    assert "slot_quote_requirement_unimplemented:payment_clause_present" in (
        rule.publication_blockers
    )


def test_applicability_dimension_requires_declared_fact(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["dependencies"]["facts"].remove("contract.jurisdiction")
    path = tmp_path / "undeclared-jurisdiction-fact.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(
        RuleValidationError,
        match="applicability_dimension_fact_not_declared:contract.jurisdiction",
    ):
        load_development_rule(path)


def test_empty_applicability_dimension_stays_undetermined(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["applicability"]["jurisdictions"] = []
    path = tmp_path / "empty-jurisdictions.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    rule = load_development_rule(path)

    plan = rule.plan(
        {
            "contract.party_role": "customer",
            "contract.contract_type": "service",
            "contract.jurisdiction": "CN-mainland",
        }
    )

    assert plan.applicability == "undetermined"
    assert "applicability_dimension_empty:contract.jurisdiction" in plan.publication_blockers


def test_payment_candidate_semantic_tasks_are_engineering_covered_but_not_published() -> None:
    rule = load_development_rule(RULE_PATH)

    assert "semantic_task_unimplemented" not in rule.publication_blockers
    assert "approved_evaluation_missing" in rule.publication_blockers
    assert "publication_approval_missing" in rule.publication_blockers


def test_compiled_rule_execution_data_is_deeply_immutable() -> None:
    rule = load_development_rule(RULE_PATH)
    payment_schedule = next(slot for slot in rule.slots if slot.slot_id == "payment_schedule")

    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        rule.applicability_expression["all"][0]["eq"][1] = "supplier"
    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        payment_schedule.value_schema["items"]["properties"]["percentage"]["pattern"] = ".*"
    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        rule.minimum_judgment_rules[0].when["eq"][1] = "supplier"


def test_compiled_rule_preserves_execution_budgets_and_blocks_unimplemented_token_budget(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["execution"].update({"max_rounds": 7, "timeout_seconds": 90, "token_budget": 16000})
    path = tmp_path / "custom-execution-budgets.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert (rule.max_rounds, rule.timeout_seconds, rule.token_budget) == (7, 90, 16000)
    assert "execution_budget_unimplemented:token_budget" in rule.publication_blockers
    assert resolve_review_execution_budget(rule, worker_timeout_ceiling=120).max_rounds == 7
    assert resolve_review_execution_budget(rule, worker_timeout_ceiling=120).timeout_seconds == 90
    assert resolve_review_execution_budget(rule, worker_timeout_ceiling=30).timeout_seconds == 30


def test_semantic_task_and_candidate_slot_contracts_cannot_drift(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["execution"]["semantic_tasks"].remove("classify_payment_triggers")
    path = tmp_path / "semantic-task-slot-mismatch.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(
        RuleValidationError,
        match="semantic_task_slot_contract_mismatch:classify_payment_triggers",
    ):
        load_development_rule(path)


def test_expression_supports_only_declared_deterministic_operators() -> None:
    assert (
        evaluate_expression({"all": [{"eq": ["x", "yes"]}, {"exists": "y"}]}, {"x": "yes"}) is False
    )
    assert evaluate_expression({"gte": ["ratio", "0.25"]}, {"ratio": "0.30"}) is True
    assert evaluate_expression({"lte": ["ratio", "0.25"]}, {}) is None
    assert (
        evaluate_expression(
            {"eq": ["terms.acceptance_required", True]},
            {"terms": {"acceptance_required": True}},
        )
        is True
    )
    assert (
        evaluate_expression(
            {"eq": ["payment_triggers.acceptance_required", False]},
            {"payment_triggers": [{"acceptance_required": False}]},
        )
        is None
    )
    assert (
        evaluate_expression(
            {"eq": ["payment_triggers.acceptance_required", False]},
            {"payment_triggers": []},
        )
        is None
    )
    assert evaluate_expression({"gte": ["ratio", "0.25"]}, {"ratio": 0.3}) is None
    with pytest.raises(RuleValidationError, match="expression_operator_forbidden"):
        evaluate_expression({"python": "__import__('os')"}, {})


@pytest.mark.parametrize(
    "value",
    [True, 0.3, "not-a-number", "NaN", "Infinity", "1" * 129, None, [], {}],
)
@pytest.mark.parametrize("operator", ["gte", "lte"])
def test_numeric_comparison_invalid_fact_is_unknown(value: Any, operator: str) -> None:
    assert evaluate_expression({operator: ["amount", "100"]}, {"amount": value}) is None


@pytest.mark.parametrize(
    "target",
    [True, 0.25, "not-a-number", "NaN", "Infinity", "1" * 129, None, [], {}],
)
@pytest.mark.parametrize("operator", ["gte", "lte"])
def test_numeric_comparison_rejects_invalid_rule_literal(target: Any, operator: str) -> None:
    with pytest.raises(RuleValidationError, match="numeric_comparison_target_invalid"):
        evaluate_expression({operator: ["amount", target]}, {})


def test_numeric_comparison_invalid_fact_keeps_rule_plan_undetermined(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["applicability"]["expression"] = {"gte": ["contract.total_amount", "100"]}
    path = tmp_path / "numeric-fact-unknown.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    rule = load_development_rule(path)

    plan = rule.plan(
        {
            "contract.party_role": "customer",
            "contract.contract_type": "service",
            "contract.jurisdiction": "CN-mainland",
            "contract.total_amount": "unknown",
        }
    )

    assert plan.applicability == "undetermined"


def test_rule_rejects_invalid_numeric_comparison_literal(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["applicability"]["expression"] = {"gte": ["contract.total_amount", 100.0]}
    path = tmp_path / "invalid-numeric-literal.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RuleValidationError, match="numeric_comparison_target_invalid"):
        load_development_rule(path)


def test_expression_type_mismatch_is_unknown_not_python_coercion() -> None:
    assert evaluate_expression({"eq": ["flag", True]}, {"flag": 1}) is None
    assert (
        evaluate_expression(
            {"eq": ["nested", {"flag": True}]},
            {"nested": {"flag": 1}},
        )
        is None
    )
    assert evaluate_expression({"in": ["kind", ["service"]]}, {"kind": 1}) is None
    assert evaluate_expression({"in": ["kind", ["service"]]}, {"kind": "software"}) is False


@pytest.mark.parametrize(
    ("change", "value", "error"),
    [
        ("tool", "filesystem.read", "tool_not_allowlisted"),
        ("slot_tool", "network.fetch", "slot_tool_not_in_rule_allowlist"),
        ("cycle", None, "slot_dependency_cycle"),
        ("missing_slot_dependency", None, "slot_dependency_missing"),
        ("schema", "mystery", "value_schema_invalid"),
        ("expression", {"eval": "True"}, "expression_operator_forbidden"),
        ("status", "published", "rule_not_approved_for_development"),
        ("unknown", "execute_python", "unknown_rule_fields"),
        ("reference", ["enterprise.payment-policy"], "enterprise_position_refs_must_be_versioned"),
        (
            "reference",
            ["enterprise.payment-policy/current"],
            "enterprise_position_refs_must_be_versioned",
        ),
        (
            "reference",
            ["legal.civil-code/v1"],
            "knowledge_reference_category_mismatch",
        ),
        (
            "legal_reference",
            ["enterprise.payment-policy/v1"],
            "knowledge_reference_category_mismatch",
        ),
        ("unknown_applicability", None, "unknown_applicability_fields"),
        ("unknown_execution", None, "unknown_execution_fields"),
        ("unknown_risk_rule", None, "unknown_minimum_judgment_rule_fields"),
        ("unknown_slot_evidence", None, "unknown_slot_evidence_policy_fields"),
        (
            "unknown_slot_validation_rule",
            None,
            "slot_validation_rule_not_allowlisted",
        ),
        ("unsupported_step", None, "deterministic_step_not_allowlisted"),
        ("invalid_risk_outcome_type", None, "minimum_judgment_rule_outcome_invalid"),
        ("invalid_risk_severity_type", None, "minimum_judgment_rule_severity_invalid"),
        ("invalid_slot_type", None, "slot_metadata_invalid"),
        (
            "undeclared_applicability_reference",
            None,
            "applicability_reference_undeclared",
        ),
        (
            "undeclared_slot_condition_reference",
            None,
            "payment_schedule_required_when_reference_undeclared",
        ),
        (
            "undeclared_risk_reference",
            None,
            "minimum_judgment_rule_reference_undeclared",
        ),
        (
            "reference",
            ["enterprise.payment-policy/mvp-v1"] * 2,
            "enterprise_position_refs_duplicate",
        ),
    ],
)
def test_invalid_rule_fails_closed(tmp_path: Path, change: str, value: Any, error: str) -> None:
    path = _modified_rule(tmp_path, change, value)
    with pytest.raises(RuleValidationError, match=error):
        load_development_rule(path)


def test_duplicate_yaml_key_is_rejected(tmp_path: Path) -> None:
    source = RULE_PATH.read_text(encoding="utf-8")
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        source.replace(
            "status: approved_for_development",
            "status: approved_for_development\nstatus: published",
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuleValidationError, match="duplicate_or_nonstring_yaml_key"):
        load_development_rule(path)


def test_plan_rejects_float_facts() -> None:
    rule = load_development_rule(RULE_PATH)
    with pytest.raises(RuleValidationError, match="facts_must_not_contain_float"):
        rule.plan(
            {
                "contract.party_role": "customer",
                "contract.contract_type": "service",
                "contract.jurisdiction": "CN-mainland",
                "amount": 0.1,
            }
        )


@pytest.mark.parametrize(
    "facts",
    [
        {1: "top-level"},
        {"nested": {1: "nested"}},
        {"items": [{True: "inside array"}]},
    ],
)
def test_canonical_facts_reject_non_string_object_keys(facts: dict[Any, Any]) -> None:
    with pytest.raises(RuleValidationError, match="facts_must_have_string_keys"):
        canonical_rule_facts(facts)


def test_populated_development_metadata_does_not_remove_release_gates(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document.update(
        {
            "knowledge_domain": "legal",
            "basis_kind": "legal_validity_question",
            "applicable_time_basis": "signing_date",
            "review_owner_role": "legal_reviewer",
            "normative_refs": ["legal.civil-code/v1"],
        }
    )
    path = tmp_path / "complete-metadata.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert rule.knowledge_domain == "legal"
    assert rule.basis_kind == "legal_validity_question"
    assert rule.applicable_time_basis == "signing_date"
    assert rule.review_owner_role == "legal_reviewer"
    assert rule.publication_blockers == (
        "dependent_check_completion_proof_unimplemented:legal.price.definition",
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
        "rule_not_published",
        "knowledge_source_content_unresolved",
        "approved_evaluation_missing",
        "publication_approval_missing",
        "rollback_target_missing",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("knowledge_domain", 1),
        ("basis_kind", False),
        ("applicable_time_basis", "  "),
        ("review_owner_role", []),
    ],
)
def test_rule_rejects_malformed_optional_metadata(
    field: str, value: object, tmp_path: Path
) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document[field] = value
    path = tmp_path / "malformed-metadata.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RuleValidationError, match=f"{field}_invalid"):
        load_development_rule(path)


def test_expression_path_semantics_blocker_is_absent_without_nested_slot_references(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["risk_policy"]["minimum_judgment_rules"][1]["when"] = {
        "eq": ["payment_clause_present", False]
    }
    path = tmp_path / "no-nested-slot-path.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert "expression_path_semantics_unresolved" not in rule.publication_blockers


def test_expression_path_semantics_allow_schema_declared_object_paths(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    payment_schedule = next(
        slot for slot in document["slots"] if slot["slot_id"] == "payment_schedule"
    )
    payment_schedule["value_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["terms"],
        "properties": {
            "terms": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"acceptance_required": {"type": "boolean"}},
            }
        },
    }
    document["risk_policy"]["minimum_judgment_rules"] = [
        {
            "when": {"eq": ["payment_schedule.terms.acceptance_required", False]},
            "judgment": "issue_found",
            "reason": "schema-declared nested object path",
        }
    ]
    path = tmp_path / "object-path.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert "expression_path_semantics_unresolved" not in rule.publication_blockers


def test_human_confirmation_evidence_is_a_publication_blocker(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "human-confirmation-evidence.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert "human_confirmation_evidence_unimplemented" in rule.publication_blockers


def test_human_confirmation_evidence_blocker_is_absent_when_not_declared(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    for slot in document["slots"]:
        slot["evidence_policy"]["kinds"] = [
            kind for kind in slot["evidence_policy"]["kinds"] if kind != "human_confirmation"
        ]
    path = tmp_path / "no-human-confirmation-evidence.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert "human_confirmation_evidence_unimplemented" not in rule.publication_blockers


def test_minimum_judgment_policy_is_compiled_without_clearing_other_release_gates(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    blocked_path = tmp_path / "minimum-policy-declared.yaml"
    blocked_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    rule = load_development_rule(blocked_path)
    assert len(rule.minimum_judgment_rules) == 2
    assert rule.minimum_judgment_rules[0].judgment == "needs_material"
    assert rule.human_review_when == (
        "payment_schedule_conflicted",
        "payment_total_not_reconcilable",
        "enterprise_threshold_missing",
    )
    assert "approved_evaluation_missing" in rule.publication_blockers
    assert "minimum_judgment_policy_unimplemented" not in rule.publication_blockers
    assert (
        "slot_validation_rule_unimplemented:judgment_must_follow_minimum_policy"
        in rule.publication_blockers
    )
    assert (
        "slot_validation_rule_unimplemented:derive_with_decimal_executor_only"
        in rule.publication_blockers
    )

    document["risk_policy"]["minimum_judgment_rules"] = []
    unblocked_path = tmp_path / "minimum-policy-absent.yaml"
    unblocked_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    assert load_development_rule(unblocked_path).minimum_judgment_rules == ()


def test_human_review_conditions_are_retained_and_block_unimplemented_execution(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "human-review-conditions.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert rule.human_review_when == (
        "payment_schedule_conflicted",
        "payment_total_not_reconcilable",
        "enterprise_threshold_missing",
    )
    assert {
        f"human_review_condition_unimplemented:{condition}" for condition in rule.human_review_when
    }.issubset(rule.publication_blockers)


def test_absent_human_review_conditions_do_not_add_condition_blockers(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["risk_policy"]["human_review_when"] = []
    path = tmp_path / "no-human-review-conditions.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert rule.human_review_when == ()
    assert not any(
        blocker.startswith("human_review_condition_unimplemented:")
        for blocker in rule.publication_blockers
    )


def test_human_review_condition_ids_must_be_valid_identifiers(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["risk_policy"]["human_review_when"] = ["Needs Human Review"]
    path = tmp_path / "invalid-human-review-condition.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RuleValidationError, match="human_review_when_identifier_invalid"):
        load_development_rule(path)


def test_rule_execution_blockers_clear_only_for_implemented_components(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    execution = document["execution"]
    execution["deterministic_steps"] = [
        "verify_input_snapshot",
        "locate_payment_candidates",
        "calculate_payment_totals",
        "apply_minimum_policy",
        "validate_evidence",
    ]
    execution["tool_allowlist"] = ["document.locate_clause"]
    for slot in document["slots"]:
        slot["allowed_tools"] = ["document.locate_clause"]
    path = tmp_path / "implemented-execution-components.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert "deterministic_step_unimplemented" not in rule.publication_blockers
    assert "semantic_task_unimplemented" not in rule.publication_blockers
    assert "rule_tool_unimplemented" not in rule.publication_blockers


def test_exact_decimal_tool_is_covered_while_payment_ratio_remains_a_release_blocker(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["execution"]["tool_allowlist"] = [
        "document.locate_clause",
        "calculation.sum_decimal",
    ]
    for slot in document["slots"]:
        slot["allowed_tools"] = [
            tool for tool in slot["allowed_tools"] if tool != "calculation.payment_ratio"
        ]
    path = tmp_path / "sum-decimal-implemented.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    rule = load_development_rule(path)

    assert not any(
        item.startswith("rule_tool_unimplemented:") for item in rule.publication_blockers
    )
    assert "rule_not_published" in rule.publication_blockers
