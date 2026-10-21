import json
import os
import shutil
import stat
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import yaml

from contract_app.rules.compiler import load_development_rule
from contract_app.rules.evaluation import (
    MAX_EVALUATION_CASES,
    MAX_EVALUATION_FIXTURE_BYTES,
    MAX_EVALUATION_FIXTURE_TOTAL_BYTES,
    MAX_EVALUATION_OBSERVATION_LINE_BYTES,
    MAX_EVALUATION_OBSERVATIONS_BYTES,
    MAX_EVALUATION_REPORT_BYTES,
    EvaluationDataInvalid,
    EvaluationSuite,
    load_evaluation_suite,
    render_evaluation_report_markdown,
    score_evaluation_suite,
    verify_evaluation_report,
)

SUITE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/eval/legal.payment_conditions.cases.yaml"
)
RULE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
)
RULE = load_development_rule(RULE_PATH)
PRICE_SUITE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/eval/legal.price.definition.dev.yaml"
)
PRICE_RULE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/rules/legal.price.definition.yaml"
)
PRICE_RULE = load_development_rule(PRICE_RULE_PATH)


def _resign_evaluation_report(report: dict[str, Any]) -> None:
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    report["report_sha256"] = sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _observed_evidence(suite: EvaluationSuite, case_id: str) -> list[dict[str, object]]:
    case = next(item for item in suite.cases if item["case_id"] == case_id)
    fixtures = {item.fixture_id: item for item in suite.fixtures_for(case_id)}
    evidence: list[dict[str, object]] = []
    for span in case["expected"]["evidence_spans"]:
        fixture = fixtures[span["fixture_id"]]
        page_text = fixture.text_for_page(span["page"])
        assert page_text is not None
        quote = page_text[span["start_offset"] : span["end_offset"]]
        evidence.append(
            {
                "fixture_id": fixture.fixture_id,
                "source_sha256": fixture.source_sha256,
                "page": span["page"],
                "start_offset": span["start_offset"],
                "end_offset": span["end_offset"],
                "quote": quote,
                "quote_sha256": sha256(quote.encode("utf-8")).hexdigest(),
            }
        )
    return evidence


def test_development_suite_scores_records_but_never_passes_without_approved_thresholds() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    observations: list[dict[str, Any]] = [
        {
            "case_id": "payment-positive-001",
            "applicability": "applicable",
            "judgment": "passed",
            "required_slots": [
                "payment_clause_present",
                "payment_schedule",
                "payment_triggers",
                "prepayment_ratio",
                "payment_risk_judgment",
            ],
            "slot_values": {
                "payment_clause_present": {"state": "filled", "value": True},
                "payment_schedule": {
                    "state": "filled",
                    "value": [
                        {"trigger": "验收", "amount_type": "percentage", "percentage": "100"}
                    ],
                },
                "payment_triggers": {
                    "state": "filled",
                    "value": [
                        {
                            "stage": "付款条件",
                            "conditions": ["验收合格", "收到合规发票"],
                            "acceptance_required": True,
                            "invoice_required": True,
                        }
                    ],
                },
            },
            "direct_evidence": True,
            "human_task": None,
            "evidence": _observed_evidence(suite, "payment-positive-001"),
        },
        {
            "case_id": "payment-adverse-001",
            "applicability": "applicable",
            "judgment": "issue_found",
            "severity": "high",
            "prepayment_ratio": "1.0",
            "slot_values": {
                "payment_clause_present": {"state": "filled", "value": True},
                "payment_schedule": {
                    "state": "filled",
                    "value": [
                        {
                            "trigger": "签约后三日内",
                            "amount_type": "percentage",
                            "percentage": "100",
                        }
                    ],
                },
                "payment_triggers": {
                    "state": "filled",
                    "value": [{"stage": "签约后三日内", "conditions": ["合同签署后三日内"]}],
                },
            },
            "direct_evidence": True,
            "human_task": "approval",
            "evidence": _observed_evidence(suite, "payment-adverse-001"),
        },
        {
            "case_id": "payment-missing-001",
            "applicability": "applicable",
            "judgment": "needs_material",
            "slot_status": {"payment_schedule": "needs_material"},
            "slot_values": {"payment_schedule": {"state": "needs_material", "value": None}},
            "human_task": "material",
            "evidence": _observed_evidence(suite, "payment-missing-001"),
        },
        {
            "case_id": "payment-conflict-001",
            "applicability": "applicable",
            "judgment": "human_review",
            "slot_status": {"payment_triggers": "conflicted"},
            "slot_values": {"payment_triggers": {"state": "conflicted", "value": None}},
            "human_task": "legal_review",
            "evidence": _observed_evidence(suite, "payment-conflict-001"),
        },
    ]
    expected_by_id = {case["case_id"]: case["expected"] for case in suite.cases}
    for observation in observations:
        observation["slot_values"] = deepcopy(expected_by_id[observation["case_id"]]["slot_values"])

    report = score_evaluation_suite(suite, observations, rule=RULE)

    assert report["report_version"] == "1.2"
    assert report["suite"]["level"] == "development"
    assert report["suite"]["source_sha256"] == suite.source_sha256
    assert report["suite"]["fixture_manifest_sha256"] == suite.fixture_manifest_sha256
    assert report["metrics"] == {
        "case_count": 4,
        "observation_count": 4,
        "passed_case_count": 4,
        "failed_case_count": 0,
        "case_pass_rate": 1.0,
        "missing_case_kinds": ["boundary", "security"],
        "by_case_kind": {
            "conflict": {
                "case_count": 1,
                "observation_count": 1,
                "passed_case_count": 1,
                "failed_case_count": 0,
                "case_pass_rate": 1.0,
            },
            "missing_material": {
                "case_count": 1,
                "observation_count": 1,
                "passed_case_count": 1,
                "failed_case_count": 0,
                "case_pass_rate": 1.0,
            },
            "negative": {
                "case_count": 1,
                "observation_count": 1,
                "passed_case_count": 1,
                "failed_case_count": 0,
                "case_pass_rate": 1.0,
            },
            "positive": {
                "case_count": 1,
                "observation_count": 1,
                "passed_case_count": 1,
                "failed_case_count": 0,
                "case_pass_rate": 1.0,
            },
        },
    }
    assert report["cases"][0] == {
        "case_id": "payment-positive-001",
        "kind": "positive",
        "observed": True,
        "passed": True,
        "mismatches": [],
    }
    assert report["gate_status"] == "blocked_no_approved_thresholds"
    assert verify_evaluation_report(report) == report
    assert (
        score_evaluation_suite(suite, list(reversed(observations)), rule=RULE)[
            "observations_sha256"
        ]
        == (report["observations_sha256"])
    )
    hashable_report = dict(report)
    report_hash = hashable_report.pop("report_sha256")
    assert (
        report_hash
        == sha256(
            json.dumps(
                hashable_report,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    )
    assert (
        score_evaluation_suite(suite, list(reversed(observations)), rule=RULE)["report_sha256"]
        == report_hash
    )


def test_evaluation_reports_missing_result_and_rejects_duplicate_or_unknown_records() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    report = score_evaluation_suite(suite, [], rule=RULE)
    assert report["metrics"]["failed_case_count"] == 4
    assert report["cases"][0]["mismatches"] == ["result_missing"]
    assert report["cases"][0]["observed"] is False
    assert report["metrics"]["by_case_kind"]["positive"]["observation_count"] == 0
    assert report["metrics"]["by_case_kind"]["positive"]["failed_case_count"] == 1
    assert report["metrics"]["missing_case_kinds"] == ["boundary", "security"]
    assert verify_evaluation_report(report) == report

    duplicate = [{"case_id": "payment-positive-001"}] * 2
    with pytest.raises(EvaluationDataInvalid, match="evaluation_observation_duplicate_case"):
        score_evaluation_suite(suite, duplicate, rule=RULE)
    with pytest.raises(EvaluationDataInvalid, match="evaluation_observation_unknown_case"):
        score_evaluation_suite(suite, [{"case_id": "not-in-suite"}], rule=RULE)


def test_evaluation_report_v12_verifies_missing_kinds_and_accepts_prior_versions() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    report = score_evaluation_suite(suite, [], rule=RULE)

    inconsistent = deepcopy(report)
    positive = inconsistent["metrics"]["by_case_kind"]["positive"]
    positive.update(
        observation_count=1,
        case_pass_rate=1.0,
        passed_case_count=1,
        failed_case_count=0,
    )
    _resign_evaluation_report(inconsistent)
    with pytest.raises(EvaluationDataInvalid, match="evaluation_report_metrics_invalid"):
        verify_evaluation_report(inconsistent)

    inconsistent_coverage = deepcopy(report)
    inconsistent_coverage["metrics"]["missing_case_kinds"].remove("security")
    _resign_evaluation_report(inconsistent_coverage)
    with pytest.raises(EvaluationDataInvalid, match="evaluation_report_metrics_invalid"):
        verify_evaluation_report(inconsistent_coverage)

    prior = deepcopy(report)
    prior["report_version"] = "1.1"
    prior["metrics"].pop("missing_case_kinds")
    _resign_evaluation_report(prior)
    assert verify_evaluation_report(prior) == prior

    legacy = deepcopy(report)
    legacy["report_version"] = "1.0"
    legacy["metrics"].pop("by_case_kind")
    legacy["metrics"].pop("missing_case_kinds")
    for case in legacy["cases"]:
        case.pop("kind")
        case.pop("observed")
    _resign_evaluation_report(legacy)
    assert verify_evaluation_report(legacy) == legacy


def test_markdown_report_renders_verified_summary_and_missing_observations() -> None:
    suite = load_evaluation_suite(PRICE_SUITE_PATH)
    report = score_evaluation_suite(suite, [], rule=PRICE_RULE)

    rendered = render_evaluation_report_markdown(report)

    assert "# 开发态评测报告" in rendered
    assert "缺少观察" in rendered
    assert "报告 SHA-256" in rendered
    assert "不代表真实模型质量" in rendered


def test_evaluation_suite_must_be_declared_by_the_compiled_rule(tmp_path: Path) -> None:
    document = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    document["eval_suite_refs"] = ["legal.payment_conditions.other@1.0.0"]
    rule_path = tmp_path / "different-evaluation-reference.yaml"
    rule_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    rule = load_development_rule(rule_path)
    suite = load_evaluation_suite(SUITE_PATH)

    with pytest.raises(EvaluationDataInvalid, match="evaluation_suite_not_declared_by_rule"):
        score_evaluation_suite(suite, [], rule=rule)


def test_evaluation_rejects_unknown_gold_and_observation_fields(tmp_path: Path) -> None:
    shutil.copytree(SUITE_PATH.parent / "fixtures", tmp_path / "fixtures")
    malformed_suite = tmp_path / "malformed.yaml"
    malformed_suite.write_text(
        SUITE_PATH.read_text(encoding="utf-8").replace(
            "      judgment: passed", "      judgement: passed"
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvaluationDataInvalid, match="evaluation_expectation_unknown_fields"):
        load_evaluation_suite(malformed_suite)

    malformed_suite.write_text(
        f"{SUITE_PATH.read_text(encoding='utf-8')}\nunknown: true\n",
        encoding="utf-8",
    )
    with pytest.raises(EvaluationDataInvalid, match="evaluation_suite_unknown_fields"):
        load_evaluation_suite(malformed_suite)

    suite = load_evaluation_suite(SUITE_PATH)
    with pytest.raises(EvaluationDataInvalid, match="evaluation_observation_unknown_fields"):
        score_evaluation_suite(
            suite,
            [{"case_id": "payment-positive-001", "judgement": "passed"}],
            rule=RULE,
        )


def test_evaluation_rejects_invalid_schema_values_instead_of_crashing() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    with pytest.raises(EvaluationDataInvalid, match="evaluation_observation_value_invalid"):
        score_evaluation_suite(
            suite,
            [{"case_id": "payment-positive-001", "judgment": ["passed"]}],
            rule=RULE,
        )


def test_evaluation_rejects_nested_gold_mutation_after_suite_load() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    positive_case = next(case for case in suite.cases if case["case_id"] == "payment-positive-001")
    positive_case["expected"]["judgment"] = "issue_found"

    with pytest.raises(EvaluationDataInvalid, match="evaluation_suite_mutated"):
        score_evaluation_suite(suite, [], rule=RULE)


def test_evaluation_slot_values_are_checked_against_the_compiled_rule_schema() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    invalid_schedule = [{"trigger": "验收", "amount_type": "percentage", "percentage": 100}]
    invalid_values = deepcopy(
        next(
            case["expected"]["slot_values"]
            for case in suite.cases
            if case["case_id"] == "payment-positive-001"
        )
    )
    invalid_values["payment_schedule"]["value"] = invalid_schedule
    with pytest.raises(EvaluationDataInvalid, match="evaluation_slot_value_schema_invalid"):
        score_evaluation_suite(
            suite,
            [
                {
                    "case_id": "payment-positive-001",
                    "slot_values": invalid_values,
                }
            ],
            rule=RULE,
        )


def test_evaluation_validates_slot_expectations_against_rule_when_observation_is_missing(
    tmp_path: Path,
) -> None:
    shutil.copytree(SUITE_PATH.parent / "fixtures", tmp_path / "fixtures")
    suite_document = yaml.safe_load(SUITE_PATH.read_text(encoding="utf-8"))
    suite_document["cases"][0]["expected"]["slot_values"]["payment_schedule"]["value"][0][
        "percentage"
    ] = 100
    malformed_suite_path = tmp_path / "malformed-rule-value.yaml"
    malformed_suite_path.write_text(
        yaml.safe_dump(suite_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    malformed_suite = load_evaluation_suite(malformed_suite_path)

    with pytest.raises(EvaluationDataInvalid, match="evaluation_slot_value_schema_invalid"):
        score_evaluation_suite(malformed_suite, [], rule=RULE)


def test_evaluation_scores_schema_valid_but_wrong_candidate_values_as_mismatches() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    expected = next(
        case["expected"]["slot_values"]
        for case in suite.cases
        if case["case_id"] == "payment-positive-001"
    )
    wrong_candidate = deepcopy(expected)
    wrong_candidate["payment_schedule"]["value"][0]["trigger"] = "验收后付款"

    report = score_evaluation_suite(
        suite,
        [
            {
                "case_id": "payment-positive-001",
                "slot_values": wrong_candidate,
                "evidence": _observed_evidence(suite, "payment-positive-001"),
            }
        ],
        rule=RULE,
    )

    assert report["cases"][0]["passed"] is False
    assert "mismatch:slot_values" in report["cases"][0]["mismatches"]


def test_evaluation_rejects_candidate_evidence_not_present_in_observation() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    candidate = deepcopy(
        next(
            case["expected"]["slot_values"]
            for case in suite.cases
            if case["case_id"] == "payment-positive-001"
        )
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_slot_evidence_not_in_observation"):
        score_evaluation_suite(
            suite,
            [{"case_id": "payment-positive-001", "slot_values": candidate}],
            rule=RULE,
        )


def test_evaluation_rejects_evidence_that_does_not_match_frozen_fixture() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    evidence = _observed_evidence(suite, "payment-positive-001")
    evidence[0]["quote"] = "not the fixture quote"
    with pytest.raises(EvaluationDataInvalid, match="evaluation_observation_evidence_invalid"):
        score_evaluation_suite(
            suite,
            [{"case_id": "payment-positive-001", "evidence": evidence}],
            rule=RULE,
        )
    with pytest.raises(EvaluationDataInvalid, match="evaluation_observation_value_invalid"):
        score_evaluation_suite(
            suite,
            [{"case_id": "payment-adverse-001", "prepayment_ratio": "1.5"}],
            rule=RULE,
        )


def test_evaluation_can_require_an_explicitly_empty_evidence_list() -> None:
    suite = load_evaluation_suite(PRICE_SUITE_PATH)
    case_id = "price-evidence-missing-001"
    expected_missing = score_evaluation_suite(
        suite,
        [{
            "case_id": case_id,
            "judgment": "human_review",
            "human_task": "legal_review",
            "evidence": [],
        }],
        rule=PRICE_RULE,
    )
    missing_case = next(item for item in expected_missing["cases"] if item["case_id"] == case_id)
    assert missing_case["passed"] is True

    omitted = score_evaluation_suite(
        suite,
        [{"case_id": case_id, "judgment": "human_review", "human_task": "legal_review"}],
        rule=PRICE_RULE,
    )
    omitted_case = next(item for item in omitted["cases"] if item["case_id"] == case_id)
    assert "evidence_missing_unobserved" in omitted_case["mismatches"]

    fixture = suite.fixtures_for(case_id)[0]
    quote = fixture.text_for_page(1)
    assert quote is not None
    cited = score_evaluation_suite(
        suite,
        [{
            "case_id": case_id,
            "judgment": "human_review",
            "human_task": "legal_review",
            "evidence": [{
                "fixture_id": fixture.fixture_id,
                "source_sha256": fixture.source_sha256,
                "page": 1,
                "start_offset": 0,
                "end_offset": len(quote),
                "quote": quote,
                "quote_sha256": sha256(quote.encode("utf-8")).hexdigest(),
            }],
        }],
        rule=PRICE_RULE,
    )
    cited_case = next(item for item in cited["cases"] if item["case_id"] == case_id)
    assert "mismatch:evidence_missing" in cited_case["mismatches"]


def _write_single_case_fixture_suite(
    tmp_path: Path,
    *,
    fixture_bytes: bytes,
    fixture_format: str,
    evidence_page: object = 3,
    include_slot_value: bool = False,
) -> tuple[Path, str]:
    (tmp_path / "fixtures").mkdir(exist_ok=True)
    (tmp_path / "fixtures" / "multi-page.json").write_bytes(fixture_bytes)
    pages = json.loads(fixture_bytes).get("pages", [])
    quote = pages[2] if len(pages) > 2 and isinstance(pages[2], str) else "第三页引用"
    start_offset = 0
    end_offset = len(quote)
    fixture_hash = sha256(fixture_bytes).hexdigest()
    quote_hash = sha256(quote.encode("utf-8")).hexdigest()
    evidence_span = {
        "fixture_id": "main",
        "page": evidence_page,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "quote_sha256": quote_hash,
    }
    expected: dict[str, Any] = {"evidence_spans": [evidence_span]}
    if include_slot_value:
        expected["slot_values"] = {
            "payment_clause_present": {
                "state": "filled",
                "value": True,
                "evidence_spans": [evidence_span.copy()],
            }
        }
    suite_document = {
        "suite_id": "legal.payment_conditions.seed",
        "version": "1.0.0",
        "rule_ref": f"{RULE.rule_id}@{RULE.version}",
        "status": "approved_for_development",
        "cases": [
            {
                "case_id": "multi-page-citation-001",
                "kind": "positive",
                "input_summary": "synthetic multi-page evidence coordinate test",
                "fixtures": [
                    {
                        "fixture_id": "main",
                        "path": "multi-page.json",
                        "sha256": fixture_hash,
                        "format": fixture_format,
                    }
                ],
                "expected": expected,
            }
        ],
    }
    suite_path = tmp_path / "multi-page-suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(suite_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return suite_path, quote


def test_normalized_pages_fixture_scores_evidence_on_the_original_page_number(
    tmp_path: Path,
) -> None:
    fixture_bytes = json.dumps(
        {"pages": ["第一页无证据。", "", "第三页引用"]}, ensure_ascii=False
    ).encode("utf-8")
    suite_path, quote = _write_single_case_fixture_suite(
        tmp_path,
        fixture_bytes=fixture_bytes,
        fixture_format="normalized_pages_v1",
        include_slot_value=True,
    )

    suite = load_evaluation_suite(suite_path)
    case_id = "multi-page-citation-001"
    evidence = _observed_evidence(suite, case_id)
    slot_values = next(
        case["expected"]["slot_values"] for case in suite.cases if case["case_id"] == case_id
    )
    report = score_evaluation_suite(
        suite,
        [{"case_id": case_id, "evidence": evidence, "slot_values": slot_values}],
        rule=RULE,
    )

    fixture = suite.fixtures_for(case_id)[0]
    assert fixture.format == "normalized_pages_v1"
    assert len(fixture.pages) == 3
    assert evidence[0]["page"] == 3
    assert evidence[0]["quote"] == quote
    assert report["cases"] == [
        {
            "case_id": case_id,
            "kind": "positive",
            "observed": True,
            "passed": True,
            "mismatches": [],
        }
    ]
    assert report["gate_status"] == "blocked_no_approved_thresholds"


@pytest.mark.parametrize("page", [0, 4, True])
def test_normalized_pages_fixture_rejects_invalid_expected_page_numbers(
    tmp_path: Path, page: object
) -> None:
    fixture_bytes = json.dumps(
        {"pages": ["第一页引用", "第二页引用", "第三页引用"]}, ensure_ascii=False
    ).encode("utf-8")
    suite_path, _ = _write_single_case_fixture_suite(
        tmp_path,
        fixture_bytes=fixture_bytes,
        fixture_format="normalized_pages_v1",
        evidence_page=page,
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_evidence_expectation_invalid"):
        load_evaluation_suite(suite_path)


@pytest.mark.parametrize(
    "fixture_bytes",
    [
        b'{"pages": ["one"], "pages": ["two"]}',
        b'{"pages": []}',
        b'{"pages": ["one", 2]}',
        b'{"pages": ["\\ud800"]}',
    ],
)
def test_normalized_pages_fixture_rejects_ambiguous_or_invalid_documents(
    tmp_path: Path, fixture_bytes: bytes
) -> None:
    suite_path, _ = _write_single_case_fixture_suite(
        tmp_path, fixture_bytes=fixture_bytes, fixture_format="normalized_pages_v1"
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_fixture_format_invalid"):
        load_evaluation_suite(suite_path)


def test_normalized_pages_fixture_enforces_the_page_count_limit(tmp_path: Path) -> None:
    fixture_bytes = json.dumps({"pages": [""] * 10_001}).encode("utf-8")
    suite_path, _ = _write_single_case_fixture_suite(
        tmp_path, fixture_bytes=fixture_bytes, fixture_format="normalized_pages_v1"
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_fixture_format_invalid"):
        load_evaluation_suite(suite_path)


def test_evaluation_observation_rejects_boolean_page_even_when_it_aliases_page_one() -> None:
    suite = load_evaluation_suite(SUITE_PATH)
    evidence = _observed_evidence(suite, "payment-positive-001")
    evidence[0]["page"] = True

    with pytest.raises(EvaluationDataInvalid, match="evaluation_observation_evidence_invalid"):
        score_evaluation_suite(
            suite,
            [{"case_id": "payment-positive-001", "evidence": evidence}],
            rule=RULE,
        )


def test_forbidden_judgment_fails_even_when_other_expected_fields_match(tmp_path: Path) -> None:
    shutil.copytree(SUITE_PATH.parent / "fixtures", tmp_path / "fixtures")
    suite_document = yaml.safe_load(SUITE_PATH.read_text(encoding="utf-8"))
    suite_document["cases"] = [
        case for case in suite_document["cases"] if case["case_id"] == "payment-missing-001"
    ]
    suite_path = tmp_path / "single-case.yaml"
    suite_path.write_text(
        yaml.safe_dump(suite_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    suite = load_evaluation_suite(suite_path)
    report = score_evaluation_suite(
        suite,
        [
            {
                "case_id": "payment-missing-001",
                "applicability": "applicable",
                "judgment": "passed",
                "slot_status": {"payment_schedule": "needs_material"},
                "human_task": "material",
            }
        ],
        rule=RULE,
    )
    assert report["cases"][0]["passed"] is False
    assert "forbidden_judgment" in report["cases"][0]["mismatches"]


def test_cli_scores_observations_but_returns_blocked_gate_without_approved_thresholds(
    tmp_path: Path,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    observations_path.write_text("", encoding="utf-8")
    report_path = tmp_path / "evaluation-report.json"
    markdown_path = tmp_path / "evaluation-report.md"
    script = Path(__file__).parents[1] / "scripts/evaluate_rules.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--suite",
            str(SUITE_PATH),
            "--rule",
            str(RULE_PATH),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
            "--markdown-report",
            str(markdown_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert '"gate_status": "blocked_no_approved_thresholds"' in completed.stdout
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    assert report["report_sha256"]
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert "# 开发态评测报告" in markdown_path.read_text(encoding="utf-8")
    assert "报告 SHA-256" in markdown_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(markdown_path.stat().st_mode) == 0o600

    replay = subprocess.run(
        [
            sys.executable,
            str(script),
            "--suite",
            str(SUITE_PATH),
            "--rule",
            str(RULE_PATH),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert replay.returncode == 1
    assert "evaluation_report_already_exists" in replay.stderr
    assert report_path.read_bytes() == report_bytes

    verifier = Path(__file__).parents[1] / "scripts/verify_evaluation_report.py"
    verified = subprocess.run(
        [sys.executable, str(verifier), str(report_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0
    assert '"verified": true' in verified.stdout
    assert json.loads(verified.stdout)["missing_case_kinds"] == ["boundary", "security"]

    tampered_path = tmp_path / "tampered-report.json"
    report["suite"]["version"] = "tampered"
    tampered_path.write_text(json.dumps(report), encoding="utf-8")
    rejected = subprocess.run(
        [sys.executable, str(verifier), str(tampered_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 1
    assert "evaluation_report_hash_mismatch" in rejected.stderr

    duplicate_path = tmp_path / "duplicate-report.json"
    duplicate_path.write_text('{"report_version":"1.0","report_version":"1.0"}')
    duplicate = subprocess.run(
        [sys.executable, str(verifier), str(duplicate_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert duplicate.returncode == 1
    assert "evaluation_report_duplicate_json_key" in duplicate.stderr


@pytest.mark.parametrize(
    ("observation", "error_code"),
    [
        (
            '{"case_id":"payment-positive-001","case_id":"payment-adverse-001"}',
            "evaluation_observation_duplicate_json_key",
        ),
        (
            '{"case_id":"payment-positive-001","prepayment_ratio":NaN}',
            "evaluation_observation_nonstandard_number:NaN",
        ),
    ],
)
def test_evaluation_cli_rejects_ambiguous_or_nonstandard_jsonl(
    tmp_path: Path, observation: str, error_code: str
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    observations_path.write_text(observation + "\n", encoding="utf-8")
    report_path = tmp_path / "evaluation-report.json"
    script = Path(__file__).parents[1] / "scripts/evaluate_rules.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--suite",
            str(SUITE_PATH),
            "--rule",
            str(RULE_PATH),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert error_code in completed.stderr
    assert not report_path.exists()


def test_evaluation_suite_size_limit_is_applied_during_read(tmp_path: Path) -> None:
    suite_path = tmp_path / "oversized-suite.yaml"
    suite_path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))

    with pytest.raises(EvaluationDataInvalid, match="evaluation_suite_too_large"):
        load_evaluation_suite(suite_path)


def test_evaluation_suite_rejects_excessive_yaml_nesting(tmp_path: Path) -> None:
    suite_path = tmp_path / "nested-suite.yaml"
    source = SUITE_PATH.read_text(encoding="utf-8")
    notes_start = source.index("notes: ")
    notes_end = source.index("\n", notes_start)
    nested_value = "value"
    for _ in range(70):
        nested_value = f"[{nested_value}]"
    nested_source = source[:notes_start] + f"notes: {nested_value}" + source[notes_end:]
    suite_path.write_text(nested_source, encoding="utf-8")

    with pytest.raises(EvaluationDataInvalid, match="evaluation_suite_yaml_invalid"):
        load_evaluation_suite(suite_path)


def test_evaluation_suite_case_count_is_bounded(tmp_path: Path) -> None:
    suite_path = tmp_path / "too-many-cases.yaml"
    suite_path.write_text(
        yaml.safe_dump(
            {
                "suite_id": "synthetic",
                "version": "1.0.0",
                "rule_ref": f"{RULE.rule_id}@{RULE.version}",
                "status": "approved_for_development",
                "cases": [{}] * (MAX_EVALUATION_CASES + 1),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_suite_too_many_cases"):
        load_evaluation_suite(suite_path)


def test_evaluation_rejects_fixture_root_symlink(tmp_path: Path) -> None:
    suite_document = yaml.safe_load(SUITE_PATH.read_text(encoding="utf-8"))
    suite_document["cases"] = [suite_document["cases"][0]]
    fixture_name = suite_document["cases"][0]["fixtures"][0]["path"]
    outside_root = tmp_path / "outside-fixtures"
    outside_root.mkdir()
    shutil.copy(SUITE_PATH.parent / "fixtures" / fixture_name, outside_root / fixture_name)
    (tmp_path / "fixtures").symlink_to(outside_root, target_is_directory=True)
    suite_path = tmp_path / "symlink-root-suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(suite_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_fixture_path_forbidden"):
        load_evaluation_suite(suite_path)


def test_evaluation_rejects_fixture_file_symlink(tmp_path: Path) -> None:
    suite_document = yaml.safe_load(SUITE_PATH.read_text(encoding="utf-8"))
    suite_document["cases"] = [suite_document["cases"][0]]
    fixture = suite_document["cases"][0]["fixtures"][0]
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    actual = fixture_root / "actual.txt"
    shutil.copy(SUITE_PATH.parent / "fixtures" / fixture["path"], actual)
    (fixture_root / "alias.txt").symlink_to(actual)
    fixture["path"] = "alias.txt"
    suite_path = tmp_path / "symlink-fixture-suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(suite_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_fixture_path_forbidden"):
        load_evaluation_suite(suite_path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files are unavailable")
def test_evaluation_rejects_fifo_fixture(tmp_path: Path) -> None:
    suite_document = yaml.safe_load(SUITE_PATH.read_text(encoding="utf-8"))
    suite_document["cases"] = [suite_document["cases"][0]]
    suite_document["cases"][0]["fixtures"][0]["path"] = "pipe.txt"
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    os.mkfifo(fixture_root / "pipe.txt")
    suite_path = tmp_path / "fifo-fixture-suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(suite_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_fixture_path_forbidden"):
        load_evaluation_suite(suite_path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files are unavailable")
def test_evaluation_suite_rejects_fifo_source_without_waiting_for_a_writer(
    tmp_path: Path,
) -> None:
    suite_path = tmp_path / "suite.pipe"
    os.mkfifo(suite_path)

    with pytest.raises(EvaluationDataInvalid, match="evaluation_suite_unavailable"):
        load_evaluation_suite(suite_path)


def test_evaluation_fixture_size_limit_is_applied_during_read(tmp_path: Path) -> None:
    fixture_bytes = b"x" * (MAX_EVALUATION_FIXTURE_BYTES + 1)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "oversized.txt").write_bytes(fixture_bytes)
    suite_document = yaml.safe_load(SUITE_PATH.read_text(encoding="utf-8"))
    suite_document["cases"] = [suite_document["cases"][0]]
    fixture = suite_document["cases"][0]["fixtures"][0]
    fixture["path"] = "oversized.txt"
    fixture["sha256"] = sha256(fixture_bytes).hexdigest()
    suite_path = tmp_path / "oversized-fixture-suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(suite_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_fixture_too_large"):
        load_evaluation_suite(suite_path)


def test_evaluation_suite_rejects_aggregate_fixture_size_over_limit(tmp_path: Path) -> None:
    fixture_bytes = b"x" * MAX_EVALUATION_FIXTURE_BYTES
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "one-megabyte.txt").write_bytes(fixture_bytes)
    suite_document = yaml.safe_load(SUITE_PATH.read_text(encoding="utf-8"))
    suite_document["cases"] = [suite_document["cases"][0]]
    case = suite_document["cases"][0]
    case["fixtures"] = [
        {
            "fixture_id": "main" if index == 0 else f"extra-{index}",
            "path": "one-megabyte.txt",
            "sha256": sha256(fixture_bytes).hexdigest(),
            "format": "normalized_text_v1",
        }
        for index in range(MAX_EVALUATION_FIXTURE_TOTAL_BYTES // MAX_EVALUATION_FIXTURE_BYTES + 1)
    ]
    suite_path = tmp_path / "aggregate-fixture-suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(suite_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(EvaluationDataInvalid, match="evaluation_fixtures_total_too_large"):
        load_evaluation_suite(suite_path)


def test_evaluation_cli_bounds_observation_file_reads(tmp_path: Path) -> None:
    observations_path = tmp_path / "oversized-observations.jsonl"
    with observations_path.open("wb") as observations:
        observations.truncate(MAX_EVALUATION_OBSERVATIONS_BYTES + 1)
    report_path = tmp_path / "evaluation-report.json"
    script = Path(__file__).parents[1] / "scripts/evaluate_rules.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--suite",
            str(SUITE_PATH),
            "--rule",
            str(RULE_PATH),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "evaluation_observations_too_large" in completed.stderr
    assert not report_path.exists()


def test_evaluation_cli_bounds_single_observation_line(tmp_path: Path) -> None:
    observations_path = tmp_path / "oversized-observation-line.jsonl"
    with observations_path.open("wb") as observations:
        observations.write(b" " * (MAX_EVALUATION_OBSERVATION_LINE_BYTES + 1))
    report_path = tmp_path / "evaluation-report.json"
    script = Path(__file__).parents[1] / "scripts/evaluate_rules.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--suite",
            str(SUITE_PATH),
            "--rule",
            str(RULE_PATH),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "evaluation_observation_line_too_large:1" in completed.stderr
    assert not report_path.exists()


def test_evaluation_report_verifier_bounds_report_file_reads(tmp_path: Path) -> None:
    report_path = tmp_path / "oversized-report.json"
    with report_path.open("wb") as report:
        report.truncate(MAX_EVALUATION_REPORT_BYTES + 1)
    script = Path(__file__).parents[1] / "scripts/verify_evaluation_report.py"
    completed = subprocess.run(
        [sys.executable, str(script), str(report_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "evaluation_report_too_large" in completed.stderr
