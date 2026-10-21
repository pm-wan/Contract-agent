"""Deterministic scoring for reviewed rule-evaluation result records.

This module scores supplied observations only. It does not call a model, create
gold labels, or infer release thresholds from the development seed suite.
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator

from contract_app.bounded_files import read_bounded_file
from contract_app.rules.compiler import (
    CompiledRule,
    _UniqueKeyLoader,
    _validate_yaml_source_structure,
)

EVALUATION_CASE_KINDS = frozenset(
    {"positive", "negative", "missing_material", "conflict", "boundary", "security"}
)
_ALLOWED_SUITE_FIELDS = frozenset({"suite_id", "version", "rule_ref", "status", "notes", "cases"})
_ALLOWED_CASE_FIELDS = frozenset({"case_id", "kind", "input_summary", "fixtures", "expected"})
_ALLOWED_EXPECTATION_FIELDS = frozenset(
    {
        "applicability",
        "judgment",
        "required_slots",
        "direct_evidence_required",
        "evidence_missing",
        "human_task",
        "minimum_severity",
        "prepayment_ratio",
        "slot_status",
        "slot_values",
        "forbidden_judgments",
        "evidence_spans",
    }
)
_ALLOWED_OBSERVATION_FIELDS = frozenset(
    {
        "case_id",
        "applicability",
        "judgment",
        "required_slots",
        "direct_evidence",
        "human_task",
        "severity",
        "prepayment_ratio",
        "slot_status",
        "slot_values",
        "evidence",
    }
)
_EVALUATION_REPORT_FIELDS = frozenset(
    {
        "report_version",
        "suite",
        "observations_sha256",
        "metrics",
        "gate_status",
        "cases",
        "report_sha256",
    }
)
_EVALUATION_METRIC_FIELDS = frozenset(
    {
        "case_count",
        "observation_count",
        "passed_case_count",
        "failed_case_count",
        "case_pass_rate",
    }
)
_ALLOWED_JUDGMENTS = frozenset(
    {"passed", "issue_found", "needs_material", "undetermined", "not_applicable", "human_review"}
)
_ALLOWED_APPLICABILITY = frozenset({"applicable", "not_applicable", "undetermined"})
_ALLOWED_SLOT_STATES = frozenset(
    {"filled", "needs_material", "conflicted", "undetermined", "human_review"}
)
_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_MAX_FIXTURE_PAGES = 10_000
MAX_EVALUATION_SUITE_BYTES = 2 * 1024 * 1024
MAX_EVALUATION_FIXTURE_BYTES = 1024 * 1024
MAX_EVALUATION_FIXTURE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_EVALUATION_OBSERVATIONS_BYTES = 8 * 1024 * 1024
MAX_EVALUATION_OBSERVATION_LINE_BYTES = 1024 * 1024
MAX_EVALUATION_REPORT_BYTES = 8 * 1024 * 1024
MAX_EVALUATION_CASES = 10_000


class EvaluationDataInvalid(ValueError):
    """Evaluation suite or observation records are malformed or ambiguous."""


@dataclass(frozen=True, slots=True)
class EvaluationSuite:
    suite_id: str
    version: str
    rule_ref: str
    level: str
    cases: tuple[dict[str, Any], ...]
    source_sha256: str
    fixtures_by_case: tuple[tuple[str, tuple[EvaluationFixture, ...]], ...]
    fixture_manifest_sha256: str
    cases_sha256: str

    def fixtures_for(self, case_id: str) -> tuple[EvaluationFixture, ...]:
        return next(
            (
                fixtures
                for candidate_id, fixtures in self.fixtures_by_case
                if candidate_id == case_id
            ),
            (),
        )

    @property
    def reference(self) -> str:
        """Return the stable suite identity used by a rule's eval_suite_refs."""
        return f"{self.suite_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class EvaluationFixture:
    fixture_id: str
    path: str
    source_sha256: str
    pages: tuple[str, ...]
    format: str

    def text_for_page(self, page: object) -> str | None:
        if (
            not isinstance(page, int)
            or isinstance(page, bool)
            or page < 1
            or page > len(self.pages)
        ):
            return None
        return self.pages[page - 1]


def load_evaluation_suite(path: Path) -> EvaluationSuite:
    """Load a development suite and reject malformed or duplicate cases."""
    source = read_limited_evaluation_file(
        path,
        MAX_EVALUATION_SUITE_BYTES,
        too_large="evaluation_suite_too_large",
        unavailable="evaluation_suite_unavailable",
    )
    try:
        _validate_yaml_source_structure(source)
        document = yaml.load(source, Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise EvaluationDataInvalid("evaluation_suite_yaml_invalid") from exc
    if not isinstance(document, dict):
        raise EvaluationDataInvalid("evaluation_suite_shape_invalid")
    if unknown := document.keys() - _ALLOWED_SUITE_FIELDS:
        raise EvaluationDataInvalid(f"evaluation_suite_unknown_fields:{','.join(sorted(unknown))}")
    if document.get("status") != "approved_for_development":
        raise EvaluationDataInvalid("evaluation_suite_not_development_approved")
    if "notes" in document and not isinstance(document["notes"], str):
        raise EvaluationDataInvalid("evaluation_suite_metadata_invalid")
    suite_id, version, rule_ref = (
        document.get("suite_id"),
        document.get("version"),
        document.get("rule_ref"),
    )
    cases = document.get("cases")
    if (
        not isinstance(suite_id, str)
        or not suite_id
        or not isinstance(version, str)
        or not version
        or not isinstance(rule_ref, str)
        or not rule_ref
        or not isinstance(cases, list)
        or not cases
    ):
        raise EvaluationDataInvalid("evaluation_suite_metadata_invalid")
    if len(cases) > MAX_EVALUATION_CASES:
        raise EvaluationDataInvalid("evaluation_suite_too_many_cases")
    case_ids: set[str] = set()
    normalized_cases: list[dict[str, Any]] = []
    fixtures_by_case: list[tuple[str, tuple[EvaluationFixture, ...]]] = []
    fixture_manifest: list[dict[str, str]] = []
    fixture_bytes_total = 0
    fixture_root = _resolve_fixture_root(path.parent / "fixtures")
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationDataInvalid("evaluation_case_shape_invalid")
        if unknown := case.keys() - _ALLOWED_CASE_FIELDS:
            raise EvaluationDataInvalid(
                f"evaluation_case_unknown_fields:{','.join(sorted(unknown))}"
            )
        case_id, kind, expected = case.get("case_id"), case.get("kind"), case.get("expected")
        if (
            not isinstance(case_id, str)
            or not case_id.strip()
            or case_id in case_ids
            or not isinstance(kind, str)
            or kind not in EVALUATION_CASE_KINDS
            or not _nonempty_string(case.get("input_summary"))
            or not isinstance(expected, dict)
            or not expected
        ):
            raise EvaluationDataInvalid("evaluation_case_invalid")
        fixtures, loaded_fixture_bytes = _load_case_fixtures(
            case.get("fixtures"),
            fixture_root,
            remaining_total_bytes=MAX_EVALUATION_FIXTURE_TOTAL_BYTES - fixture_bytes_total,
        )
        fixture_bytes_total += loaded_fixture_bytes
        _validate_expectation(expected, {fixture.fixture_id: fixture for fixture in fixtures})
        case_ids.add(case_id)
        normalized_cases.append(case)
        fixtures_by_case.append((case_id, fixtures))
        fixture_manifest.extend(
            {
                "case_id": case_id,
                "fixture_id": fixture.fixture_id,
                "path": fixture.path,
                "source_sha256": fixture.source_sha256,
                "format": fixture.format,
            }
            for fixture in fixtures
        )
    canonical_manifest = json.dumps(
        sorted(fixture_manifest, key=lambda item: (item["case_id"], item["fixture_id"])),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    cases_sha256 = _evaluation_cases_sha256(normalized_cases)
    return EvaluationSuite(
        suite_id,
        version,
        rule_ref,
        "development",
        tuple(normalized_cases),
        sha256(source).hexdigest(),
        tuple(fixtures_by_case),
        sha256(canonical_manifest.encode("utf-8")).hexdigest(),
        cases_sha256,
    )


def _evaluation_cases_sha256(cases: Any) -> str:
    """Hash normalized suite cases so nested mutable data cannot drift after loading."""
    try:
        canonical_cases = json.dumps(
            cases,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvaluationDataInvalid("evaluation_suite_case_data_invalid") from exc
    return sha256(canonical_cases.encode("utf-8")).hexdigest()


def _resolve_fixture_root(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EvaluationDataInvalid("evaluation_fixture_unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise EvaluationDataInvalid("evaluation_fixture_path_forbidden")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise EvaluationDataInvalid("evaluation_fixture_unavailable") from exc


def _resolve_fixture_path(fixture_root: Path, relative_path: str) -> Path:
    candidate = fixture_root / relative_path
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise EvaluationDataInvalid("evaluation_fixture_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise EvaluationDataInvalid("evaluation_fixture_path_forbidden")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise EvaluationDataInvalid("evaluation_fixture_path_forbidden") from exc
    if not resolved.is_relative_to(fixture_root):
        raise EvaluationDataInvalid("evaluation_fixture_path_forbidden")
    return resolved


def _load_case_fixtures(
    value: Any, fixture_root: Path, *, remaining_total_bytes: int
) -> tuple[tuple[EvaluationFixture, ...], int]:
    if not isinstance(value, list) or not value:
        raise EvaluationDataInvalid("evaluation_case_fixtures_invalid")
    fixtures: list[EvaluationFixture] = []
    fixture_ids: set[str] = set()
    loaded_bytes = 0
    for item in value:
        if not isinstance(item, dict) or item.keys() != {
            "fixture_id",
            "path",
            "sha256",
            "format",
        }:
            raise EvaluationDataInvalid("evaluation_case_fixture_invalid")
        fixture_id, relative_path, expected_hash, fixture_format = (
            item["fixture_id"],
            item["path"],
            item["sha256"],
            item["format"],
        )
        if (
            not isinstance(fixture_id, str)
            or not fixture_id.strip()
            or fixture_id in fixture_ids
            or not _nonempty_string(relative_path)
            or not isinstance(relative_path, str)
            or Path(relative_path).name != relative_path
            or not _valid_hash(expected_hash)
            or not isinstance(fixture_format, str)
            or fixture_format not in {"normalized_text_v1", "normalized_pages_v1"}
        ):
            raise EvaluationDataInvalid("evaluation_case_fixture_invalid")
        candidate = _resolve_fixture_path(fixture_root, relative_path)
        remaining = remaining_total_bytes - loaded_bytes
        if remaining <= 0:
            raise EvaluationDataInvalid("evaluation_fixtures_total_too_large")
        file_limit = min(MAX_EVALUATION_FIXTURE_BYTES, remaining)
        source = read_limited_evaluation_file(
            candidate,
            file_limit,
            too_large=(
                "evaluation_fixture_too_large"
                if remaining >= MAX_EVALUATION_FIXTURE_BYTES
                else "evaluation_fixtures_total_too_large"
            ),
            unavailable="evaluation_fixture_unavailable",
        )
        loaded_bytes += len(source)
        if sha256(source).hexdigest() != expected_hash:
            raise EvaluationDataInvalid("evaluation_fixture_hash_mismatch")
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EvaluationDataInvalid("evaluation_fixture_encoding_invalid") from exc
        if fixture_format == "normalized_text_v1":
            if not text or "\r" in text:
                raise EvaluationDataInvalid("evaluation_fixture_format_invalid")
            pages = (text,)
        else:
            try:
                parsed = json.loads(text, object_pairs_hook=_unique_json_object)
            except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                raise EvaluationDataInvalid("evaluation_fixture_format_invalid") from exc
            if not isinstance(parsed, dict) or parsed.keys() != {"pages"}:
                raise EvaluationDataInvalid("evaluation_fixture_format_invalid")
            raw_pages = parsed["pages"]
            if (
                not isinstance(raw_pages, list)
                or not raw_pages
                or len(raw_pages) > _MAX_FIXTURE_PAGES
                or any(not _valid_normalized_page(page_text) for page_text in raw_pages)
            ):
                raise EvaluationDataInvalid("evaluation_fixture_format_invalid")
            pages = tuple(raw_pages)
        fixtures.append(
            EvaluationFixture(fixture_id, relative_path, expected_hash, pages, fixture_format)
        )
        fixture_ids.add(fixture_id)
    return tuple(fixtures), loaded_bytes


def read_limited_evaluation_file(
    path: Path,
    maximum_bytes: int,
    *,
    too_large: str,
    unavailable: str,
) -> bytes:
    """Read at most one byte past a declared limit before rejecting the file."""
    try:
        content = read_bounded_file(path, maximum_bytes)
    except OSError as exc:
        raise EvaluationDataInvalid(unavailable) from exc
    if len(content) > maximum_bytes:
        raise EvaluationDataInvalid(too_large)
    return content


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _valid_normalized_page(value: Any) -> bool:
    if not isinstance(value, str) or "\r" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def score_evaluation_suite(
    suite: EvaluationSuite, observations: list[dict[str, Any]], *, rule: CompiledRule
) -> dict[str, Any]:
    """Score result observations against explicit expected fields, never a threshold."""
    if _evaluation_cases_sha256(suite.cases) != suite.cases_sha256:
        raise EvaluationDataInvalid("evaluation_suite_mutated")
    if suite.rule_ref != f"{rule.rule_id}@{rule.version}":
        raise EvaluationDataInvalid("evaluation_suite_rule_reference_mismatch")
    if suite.reference not in rule.eval_suite_refs:
        raise EvaluationDataInvalid("evaluation_suite_not_declared_by_rule")
    if not _valid_hash(rule.source_sha256):
        raise EvaluationDataInvalid("evaluation_rule_hash_invalid")
    expected_ids = {case["case_id"] for case in suite.cases}
    by_id: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            raise EvaluationDataInvalid("evaluation_observation_shape_invalid")
        case_id = observation.get("case_id")
        if not isinstance(case_id, str) or case_id not in expected_ids:
            raise EvaluationDataInvalid("evaluation_observation_unknown_case")
        fixtures = suite.fixtures_for(case_id)
        fixtures_by_id = {fixture.fixture_id: fixture for fixture in fixtures}
        _validate_observation(observation, fixtures_by_id, rule)
        if case_id in by_id:
            raise EvaluationDataInvalid("evaluation_observation_duplicate_case")
        by_id[case_id] = observation

    rows: list[dict[str, Any]] = []
    for case in suite.cases:
        case_id = case["case_id"]
        fixtures = suite.fixtures_for(case_id)
        _validate_expectation(
            case["expected"],
            {fixture.fixture_id: fixture for fixture in fixtures},
            rule,
        )
        observed = by_id.get(case_id)
        if observed is None:
            rows.append(
                {
                    "case_id": case_id,
                    "kind": case["kind"],
                    "observed": False,
                    "passed": False,
                    "mismatches": ["result_missing"],
                }
            )
            continue
        mismatches = _compare_expected(
            case["expected"],
            observed,
            {fixture.fixture_id: fixture for fixture in fixtures},
            rule,
        )
        rows.append(
            {
                "case_id": case_id,
                "kind": case["kind"],
                "observed": True,
                "passed": not mismatches,
                "mismatches": mismatches,
            }
        )

    passed = sum(row["passed"] for row in rows)
    total = len(rows)
    present_case_kinds = {cast(str, case["kind"]) for case in suite.cases}
    missing_case_kinds = sorted(EVALUATION_CASE_KINDS - present_case_kinds)
    by_case_kind: dict[str, dict[str, int | float]] = {}
    for kind in sorted(present_case_kinds):
        kind_rows = [row for row in rows if row["kind"] == kind]
        kind_case_count = len(kind_rows)
        kind_passed_count = sum(row["passed"] for row in kind_rows)
        by_case_kind[kind] = {
            "case_count": kind_case_count,
            "observation_count": sum(row["observed"] for row in kind_rows),
            "passed_case_count": kind_passed_count,
            "failed_case_count": kind_case_count - kind_passed_count,
            "case_pass_rate": kind_passed_count / kind_case_count,
        }
    canonical_observations = json.dumps(
        sorted(observations, key=lambda row: row["case_id"]),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    report = {
        "report_version": "1.2",
        "suite": {
            "suite_id": suite.suite_id,
            "version": suite.version,
            "rule_ref": suite.rule_ref,
            "rule_sha256": rule.source_sha256,
            "level": suite.level,
            "source_sha256": suite.source_sha256,
            "fixture_manifest_sha256": suite.fixture_manifest_sha256,
        },
        "observations_sha256": sha256(canonical_observations.encode("utf-8")).hexdigest(),
        "metrics": {
            "case_count": total,
            "observation_count": len(observations),
            "passed_case_count": passed,
            "failed_case_count": total - passed,
            "case_pass_rate": passed / total,
            "by_case_kind": by_case_kind,
            "missing_case_kinds": missing_case_kinds,
        },
        "gate_status": "blocked_no_approved_thresholds",
        "cases": rows,
    }
    canonical_report = json.dumps(
        report,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    report["report_sha256"] = sha256(canonical_report.encode("utf-8")).hexdigest()
    return report


def verify_evaluation_report(value: Any) -> dict[str, Any]:
    """Validate a persisted report's closed schema, metrics, and canonical digest."""
    if not isinstance(value, dict) or value.keys() != _EVALUATION_REPORT_FIELDS:
        raise EvaluationDataInvalid("evaluation_report_shape_invalid")
    report_version = value.get("report_version")
    if (
        report_version not in ("1.0", "1.1", "1.2")
        or value.get("gate_status") != "blocked_no_approved_thresholds"
        or not _valid_hash(value.get("report_sha256"))
        or not _valid_hash(value.get("observations_sha256"))
    ):
        raise EvaluationDataInvalid("evaluation_report_metadata_invalid")
    suite = value.get("suite")
    if not isinstance(suite, dict) or suite.keys() != {
        "suite_id",
        "version",
        "rule_ref",
        "rule_sha256",
        "level",
        "source_sha256",
        "fixture_manifest_sha256",
    }:
        raise EvaluationDataInvalid("evaluation_report_suite_invalid")
    if any(
        not _nonempty_string(suite.get(field))
        for field in ("suite_id", "version", "rule_ref", "level")
    ) or any(
        not _valid_hash(suite.get(field))
        for field in ("rule_sha256", "source_sha256", "fixture_manifest_sha256")
    ):
        raise EvaluationDataInvalid("evaluation_report_suite_invalid")
    metrics = value.get("metrics")
    metric_fields = {
        "1.0": _EVALUATION_METRIC_FIELDS,
        "1.1": _EVALUATION_METRIC_FIELDS | {"by_case_kind"},
        "1.2": _EVALUATION_METRIC_FIELDS | {"by_case_kind", "missing_case_kinds"},
    }[report_version]
    if not isinstance(metrics, dict) or metrics.keys() != metric_fields:
        raise EvaluationDataInvalid("evaluation_report_metrics_invalid")
    overall_metrics = {field: metrics[field] for field in _EVALUATION_METRIC_FIELDS}
    if not _valid_metric_summary(overall_metrics):
        raise EvaluationDataInvalid("evaluation_report_metrics_invalid")
    case_count = cast(int, metrics["case_count"])
    observation_count = cast(int, metrics["observation_count"])
    passed_count = cast(int, metrics["passed_case_count"])
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != case_count or case_count > MAX_EVALUATION_CASES:
        raise EvaluationDataInvalid("evaluation_report_cases_invalid")
    case_ids: set[str] = set()
    actual_passed = 0
    actual_observations = 0
    counts_by_kind: dict[str, list[int]] = {}
    for case in cases:
        expected_case_fields = (
            {"case_id", "passed", "mismatches"}
            if report_version == "1.0"
            else {"case_id", "kind", "observed", "passed", "mismatches"}
        )
        if not isinstance(case, dict) or case.keys() != expected_case_fields:
            raise EvaluationDataInvalid("evaluation_report_case_invalid")
        case_id, passed, mismatches = (
            case.get("case_id"),
            case.get("passed"),
            case.get("mismatches"),
        )
        if (
            not _nonempty_string(case_id)
            or case_id in case_ids
            or not isinstance(passed, bool)
            or not isinstance(mismatches, list)
            or any(not _nonempty_string(mismatch) for mismatch in mismatches)
            or passed != (not mismatches)
        ):
            raise EvaluationDataInvalid("evaluation_report_case_invalid")
        if report_version in ("1.1", "1.2"):
            kind, observed = case.get("kind"), case.get("observed")
            if (
                not isinstance(kind, str)
                or kind not in EVALUATION_CASE_KINDS
                or not isinstance(observed, bool)
                or (not observed and mismatches != ["result_missing"])
                or (observed and "result_missing" in mismatches)
            ):
                raise EvaluationDataInvalid("evaluation_report_case_invalid")
            counts = counts_by_kind.setdefault(kind, [0, 0, 0])
            counts[0] += 1
            counts[1] += int(observed)
            counts[2] += int(passed)
            actual_observations += int(observed)
        case_ids.add(cast(str, case_id))
        actual_passed += int(passed)
    if actual_passed != passed_count:
        raise EvaluationDataInvalid("evaluation_report_metrics_invalid")
    if report_version in ("1.1", "1.2"):
        by_case_kind = metrics.get("by_case_kind")
        if (
            actual_observations != observation_count
            or not isinstance(by_case_kind, dict)
            or by_case_kind.keys() != counts_by_kind.keys()
        ):
            raise EvaluationDataInvalid("evaluation_report_metrics_invalid")
        for kind, (
            kind_case_count,
            kind_observation_count,
            kind_passed_count,
        ) in counts_by_kind.items():
            if not _valid_metric_summary(
                by_case_kind[kind],
                expected_counts=(
                    kind_case_count,
                    kind_observation_count,
                    kind_passed_count,
                    kind_case_count - kind_passed_count,
                ),
            ):
                raise EvaluationDataInvalid("evaluation_report_metrics_invalid")
    if report_version == "1.2":
        missing_case_kinds = metrics.get("missing_case_kinds")
        if (
            not isinstance(missing_case_kinds, list)
            or any(not isinstance(kind, str) for kind in missing_case_kinds)
            or missing_case_kinds != sorted(set(missing_case_kinds))
            or set(missing_case_kinds) != EVALUATION_CASE_KINDS - counts_by_kind.keys()
        ):
            raise EvaluationDataInvalid("evaluation_report_metrics_invalid")
    content = {key: item for key, item in value.items() if key != "report_sha256"}
    canonical_report = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if sha256(canonical_report.encode("utf-8")).hexdigest() != value["report_sha256"]:
        raise EvaluationDataInvalid("evaluation_report_hash_mismatch")
    return value


def render_evaluation_report_markdown(value: Any) -> str:
    """Render a verified score report as a concise, reproducible Chinese summary."""
    def cell(value: Any) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " ")
        )

    report = verify_evaluation_report(value)
    suite = report["suite"]
    metrics = report["metrics"]
    rate = metrics["case_pass_rate"]
    lines = [
        f"# 开发态评测报告：{cell(suite['suite_id'])}@{cell(suite['version'])}",
        "",
        (
            "> 本报告只汇总已提供的结构化 observations 与开发预期的匹配情况；"
            "不生成观察值，不代表真实模型质量、正式金标或发布批准。"
        ),
        "",
        "## 总览",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 案例数 | {metrics['case_count']} |",
        f"| 已提供 observations | {metrics['observation_count']} |",
        f"| 匹配 / 不匹配 | {metrics['passed_case_count']} / {metrics['failed_case_count']} |",
        f"| 描述性匹配率 | {rate:.1%} |",
        f"| 评测门状态 | `{report['gate_status']}` |",
        f"| 未覆盖类别 | {', '.join(metrics.get('missing_case_kinds', [])) or '无'} |",
        "",
        "## 逐例结果",
        "",
        "| 案例 | 类别 | 状态 | 差异 |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        observed = case.get("observed", True)
        status = "已观察且匹配" if observed and case["passed"] else (
            "已观察但不匹配" if observed else "缺少观察"
        )
        mismatches = "、".join(case["mismatches"]) or "无"
        lines.append(
            f"| {cell(case['case_id'])} | {cell(case.get('kind', '未分类'))} | "
            f"{status} | {cell(mismatches)} |"
        )
    lines.extend(
        [
            "",
            "## 完整性指纹",
            "",
            f"- 规则引用：`{cell(suite['rule_ref'])}`；规则 SHA-256：`{suite['rule_sha256']}`",
            f"- 套件 SHA-256：`{suite['source_sha256']}`；Fixture manifest SHA-256："
            f"`{suite['fixture_manifest_sha256']}`",
            f"- Observations SHA-256：`{report['observations_sha256']}`",
            f"- 报告 SHA-256：`{report['report_sha256']}`",
            "",
            "报告自哈希用于完整性核验，不是签名或审批证明。",
            "",
        ]
    )
    return "\n".join(lines)


def _valid_metric_summary(
    value: Any,
    *,
    expected_counts: tuple[int, int, int, int] | None = None,
) -> bool:
    if not isinstance(value, dict) or value.keys() != _EVALUATION_METRIC_FIELDS:
        return False
    counts = tuple(
        value.get(field)
        for field in ("case_count", "observation_count", "passed_case_count", "failed_case_count")
    )
    if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts):
        return False
    case_count, observation_count, passed_count, failed_count = (
        cast(int, count) for count in counts
    )
    pass_rate = value.get("case_pass_rate")
    return (
        case_count > 0
        and observation_count <= case_count
        and passed_count + failed_count == case_count
        and isinstance(pass_rate, (int, float))
        and not isinstance(pass_rate, bool)
        and pass_rate == passed_count / case_count
        and (expected_counts is None or counts == expected_counts)
    )


def _compare_expected(
    expected: dict[str, Any],
    observation: dict[str, Any],
    fixtures_by_id: dict[str, EvaluationFixture],
    rule: CompiledRule,
) -> list[str]:
    _validate_expectation(expected, fixtures_by_id, rule)
    mismatches: list[str] = []
    forbidden = expected.get("forbidden_judgments", [])
    judgment = observation.get("judgment")
    if not isinstance(forbidden, list) or any(not isinstance(item, str) for item in forbidden):
        raise EvaluationDataInvalid("evaluation_forbidden_judgments_invalid")
    if judgment in forbidden:
        mismatches.append("forbidden_judgment")
    for key, value in expected.items():
        if key == "forbidden_judgments":
            continue
        if key == "minimum_severity":
            actual = observation.get("severity")
            if (
                not isinstance(value, str)
                or value not in _SEVERITY_ORDER
                or not isinstance(actual, str)
                or actual not in _SEVERITY_ORDER
            ):
                mismatches.append("severity_missing_or_invalid")
            elif _SEVERITY_ORDER[actual] < _SEVERITY_ORDER[value]:
                mismatches.append("severity_below_minimum")
            continue
        if key == "evidence_missing":
            actual_evidence = observation.get("evidence", _MISSING)
            if not isinstance(actual_evidence, list):
                mismatches.append("evidence_missing_unobserved")
            elif (len(actual_evidence) == 0) is not value:
                mismatches.append("mismatch:evidence_missing")
            continue
        observation_key = "direct_evidence" if key == "direct_evidence_required" else key
        expected_value = value
        if key == "evidence_spans":
            if not _evidence_spans_contained(observation.get("evidence", []), expected_value):
                mismatches.append(f"mismatch:{key}")
            continue
        if key == "direct_evidence_required" and not isinstance(value, bool):
            raise EvaluationDataInvalid("evaluation_evidence_expectation_invalid")
        expected_value = value
        if not _contains(observation.get(observation_key, _MISSING), expected_value):
            mismatches.append(f"mismatch:{key}")
    return mismatches


def _validate_expectation(
    expected: dict[str, Any],
    fixtures_by_id: dict[str, EvaluationFixture],
    rule: CompiledRule | None = None,
) -> None:
    if unknown := expected.keys() - _ALLOWED_EXPECTATION_FIELDS:
        raise EvaluationDataInvalid(
            f"evaluation_expectation_unknown_fields:{','.join(sorted(unknown))}"
        )
    for key in ("applicability",):
        if key in expected and (
            not isinstance(expected[key], str) or expected[key] not in _ALLOWED_APPLICABILITY
        ):
            raise EvaluationDataInvalid("evaluation_expectation_invalid")
    for key in ("judgment",):
        if key in expected and (
            not isinstance(expected[key], str) or expected[key] not in _ALLOWED_JUDGMENTS
        ):
            raise EvaluationDataInvalid("evaluation_expectation_invalid")
    if "required_slots" in expected and not _string_list(expected["required_slots"]):
        raise EvaluationDataInvalid("evaluation_expectation_invalid")
    if "direct_evidence_required" in expected and not isinstance(
        expected["direct_evidence_required"], bool
    ):
        raise EvaluationDataInvalid("evaluation_expectation_invalid")
    if "evidence_missing" in expected and not isinstance(expected["evidence_missing"], bool):
        raise EvaluationDataInvalid("evaluation_expectation_invalid")
    if (
        "human_task" in expected
        and expected["human_task"] is not None
        and not _nonempty_string(expected["human_task"])
    ):
        raise EvaluationDataInvalid("evaluation_expectation_invalid")
    if "minimum_severity" in expected and (
        not isinstance(expected["minimum_severity"], str)
        or expected["minimum_severity"] not in _SEVERITY_ORDER
    ):
        raise EvaluationDataInvalid("evaluation_expectation_invalid")
    if "prepayment_ratio" in expected and not _valid_ratio(expected["prepayment_ratio"]):
        raise EvaluationDataInvalid("evaluation_expectation_invalid")
    if "slot_status" in expected:
        _validate_slot_status(expected["slot_status"])
    if "slot_values" in expected:
        _validate_slot_values(expected["slot_values"], rule, fixtures_by_id)
    if "forbidden_judgments" in expected:
        forbidden = expected["forbidden_judgments"]
        if not isinstance(forbidden, list) or any(
            not isinstance(value, str) or value not in _ALLOWED_JUDGMENTS for value in forbidden
        ):
            raise EvaluationDataInvalid("evaluation_forbidden_judgments_invalid")
    if "evidence_spans" in expected:
        spans = expected["evidence_spans"]
        if not isinstance(spans, list) or not spans:
            raise EvaluationDataInvalid("evaluation_evidence_expectation_invalid")
        seen: set[tuple[str, int, int, int, str]] = set()
        for span in spans:
            identity = _validate_span_reference(
                span, fixtures_by_id, "evaluation_evidence_expectation_invalid"
            )
            if identity in seen:
                raise EvaluationDataInvalid("evaluation_evidence_expectation_invalid")
            seen.add(identity)
    if "slot_values" in expected:
        _validate_slot_value_status_consistency(
            expected.get("slot_status"), expected["slot_values"]
        )
        slot_span_ids = _slot_evidence_span_ids(expected["slot_values"])
        suite_span_ids = {_span_identity(span) for span in expected.get("evidence_spans", [])}
        if any(
            not candidate_ids.issubset(suite_span_ids) for candidate_ids in slot_span_ids.values()
        ):
            raise EvaluationDataInvalid("evaluation_slot_evidence_not_in_suite")


def _validate_observation(
    observation: dict[str, Any],
    fixtures_by_id: dict[str, EvaluationFixture],
    rule: CompiledRule,
) -> None:
    if unknown := observation.keys() - _ALLOWED_OBSERVATION_FIELDS:
        raise EvaluationDataInvalid(
            f"evaluation_observation_unknown_fields:{','.join(sorted(unknown))}"
        )
    if not _nonempty_string(observation.get("case_id")):
        raise EvaluationDataInvalid("evaluation_observation_shape_invalid")
    if "applicability" in observation and (
        not isinstance(observation["applicability"], str)
        or observation["applicability"] not in _ALLOWED_APPLICABILITY
    ):
        raise EvaluationDataInvalid("evaluation_observation_value_invalid")
    if "judgment" in observation and (
        not isinstance(observation["judgment"], str)
        or observation["judgment"] not in _ALLOWED_JUDGMENTS
    ):
        raise EvaluationDataInvalid("evaluation_observation_value_invalid")
    if "required_slots" in observation and not _string_list(observation["required_slots"]):
        raise EvaluationDataInvalid("evaluation_observation_value_invalid")
    if "direct_evidence" in observation and not isinstance(observation["direct_evidence"], bool):
        raise EvaluationDataInvalid("evaluation_observation_value_invalid")
    if (
        "human_task" in observation
        and observation["human_task"] is not None
        and not _nonempty_string(observation["human_task"])
    ):
        raise EvaluationDataInvalid("evaluation_observation_value_invalid")
    if "severity" in observation and (
        not isinstance(observation["severity"], str)
        or observation["severity"] not in _SEVERITY_ORDER
    ):
        raise EvaluationDataInvalid("evaluation_observation_value_invalid")
    if "prepayment_ratio" in observation and not _valid_ratio(observation["prepayment_ratio"]):
        raise EvaluationDataInvalid("evaluation_observation_value_invalid")
    if "slot_status" in observation:
        _validate_slot_status(observation["slot_status"])
    if "evidence" in observation:
        _validate_evidence(observation["evidence"], fixtures_by_id)
    if "slot_values" in observation:
        _validate_slot_values(observation["slot_values"], rule, fixtures_by_id)
        _validate_slot_value_status_consistency(
            observation.get("slot_status"), observation["slot_values"]
        )
        observation_span_ids = {
            _evidence_identity(item) for item in observation.get("evidence", [])
        }
        if any(
            not candidate_ids.issubset(observation_span_ids)
            for candidate_ids in _slot_evidence_span_ids(observation["slot_values"]).values()
        ):
            raise EvaluationDataInvalid("evaluation_slot_evidence_not_in_observation")


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_nonempty_string(item) for item in value)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_ratio(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None:
        return False
    try:
        number = Decimal(value)
    except InvalidOperation:
        return False
    return number.is_finite() and Decimal("0") <= number <= Decimal("1")


def _validate_slot_status(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        raise EvaluationDataInvalid("evaluation_slot_status_invalid")
    if any(
        not _nonempty_string(slot_id)
        or not isinstance(status, str)
        or status not in _ALLOWED_SLOT_STATES
        for slot_id, status in value.items()
    ):
        raise EvaluationDataInvalid("evaluation_slot_status_invalid")


def _validate_slot_values(
    value: Any,
    rule: CompiledRule | None,
    fixtures_by_id: dict[str, EvaluationFixture],
) -> None:
    if not isinstance(value, dict) or not value:
        raise EvaluationDataInvalid("evaluation_slot_values_invalid")
    slots = {slot.slot_id: slot for slot in rule.slots} if rule is not None else None
    for slot_id, candidate in value.items():
        if (
            not _nonempty_string(slot_id)
            or not isinstance(candidate, dict)
            or candidate.keys() != {"state", "value", "evidence_spans"}
            or not isinstance(candidate.get("state"), str)
            or candidate["state"] not in _ALLOWED_SLOT_STATES
        ):
            raise EvaluationDataInvalid("evaluation_slot_values_invalid")
        state, candidate_value = candidate["state"], candidate["value"]
        if state != "filled" and candidate_value is not None:
            raise EvaluationDataInvalid("evaluation_slot_values_invalid")
        spans = candidate["evidence_spans"]
        if not isinstance(spans, list) or (state == "filled" and not spans):
            raise EvaluationDataInvalid("evaluation_slot_values_invalid")
        identities = [
            _validate_span_reference(span, fixtures_by_id, "evaluation_slot_values_invalid")
            for span in spans
        ]
        if len(identities) != len(set(identities)):
            raise EvaluationDataInvalid("evaluation_slot_values_invalid")
        if slots is None:
            continue
        slot = slots.get(slot_id)
        if slot is None or state not in slot.allowed_terminal_states:
            raise EvaluationDataInvalid("evaluation_slot_values_invalid")
        if state == "filled" and not Draft202012Validator(slot.value_schema).is_valid(
            candidate_value
        ):
            raise EvaluationDataInvalid("evaluation_slot_value_schema_invalid")


def _validate_slot_value_status_consistency(statuses: Any, values: dict[str, Any]) -> None:
    if statuses is None:
        return
    if not isinstance(statuses, dict):
        raise EvaluationDataInvalid("evaluation_slot_status_invalid")
    if any(
        slot_id in statuses and statuses[slot_id] != candidate["state"]
        for slot_id, candidate in values.items()
    ):
        raise EvaluationDataInvalid("evaluation_slot_state_mismatch")


def _slot_evidence_span_ids(
    values: dict[str, Any],
) -> dict[str, set[tuple[str, int, int, int, str]]]:
    return {
        slot_id: {_span_identity(span) for span in candidate["evidence_spans"]}
        for slot_id, candidate in values.items()
    }


def _span_identity(span: dict[str, Any]) -> tuple[str, int, int, int, str]:
    return (
        span["fixture_id"],
        span["page"],
        span["start_offset"],
        span["end_offset"],
        span["quote_sha256"],
    )


def _evidence_identity(item: dict[str, Any]) -> tuple[str, int, int, int, str]:
    return (
        item["fixture_id"],
        item["page"],
        item["start_offset"],
        item["end_offset"],
        item["quote_sha256"],
    )


def _validate_span_reference(
    span: Any,
    fixtures_by_id: dict[str, EvaluationFixture],
    error_code: str,
) -> tuple[str, int, int, int, str]:
    if not isinstance(span, dict) or span.keys() != {
        "fixture_id",
        "page",
        "start_offset",
        "end_offset",
        "quote_sha256",
    }:
        raise EvaluationDataInvalid(error_code)
    fixture_id = span["fixture_id"]
    page, start, end, quote_hash = (
        span["page"],
        span["start_offset"],
        span["end_offset"],
        span["quote_sha256"],
    )
    fixture = fixtures_by_id.get(fixture_id) if isinstance(fixture_id, str) else None
    page_text = fixture.text_for_page(page) if fixture is not None else None
    if (
        fixture is None
        or page_text is None
        or not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or not _valid_hash(quote_hash)
        or end > len(page_text)
        or sha256(page_text[start:end].encode("utf-8")).hexdigest() != quote_hash
    ):
        raise EvaluationDataInvalid(error_code)
    return (fixture_id, page, start, end, quote_hash)


def _validate_evidence(value: Any, fixtures_by_id: dict[str, EvaluationFixture]) -> None:
    if not isinstance(value, list):
        raise EvaluationDataInvalid("evaluation_observation_evidence_invalid")
    seen: set[tuple[str, int, int, int]] = set()
    for item in value:
        if not isinstance(item, dict) or item.keys() != {
            "fixture_id",
            "source_sha256",
            "page",
            "start_offset",
            "end_offset",
            "quote",
            "quote_sha256",
        }:
            raise EvaluationDataInvalid("evaluation_observation_evidence_invalid")
        fixture_id, source_sha256 = item["fixture_id"], item["source_sha256"]
        page, start, end = item["page"], item["start_offset"], item["end_offset"]
        quote, quote_sha256 = item["quote"], item["quote_sha256"]
        fixture = fixtures_by_id.get(fixture_id) if isinstance(fixture_id, str) else None
        page_text = fixture.text_for_page(page) if fixture is not None else None
        if (
            fixture is None
            or source_sha256 != fixture.source_sha256
            or page_text is None
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > len(page_text)
            or not _nonempty_string(quote)
            or page_text[start:end] != quote
            or not _valid_hash(quote_sha256)
            or sha256(quote.encode("utf-8")).hexdigest() != quote_sha256
        ):
            raise EvaluationDataInvalid("evaluation_observation_evidence_invalid")
        identity = (fixture_id, page, start, end)
        if identity in seen:
            raise EvaluationDataInvalid("evaluation_observation_evidence_duplicate")
        seen.add(identity)


def _evidence_spans_contained(observed: Any, expected: Any) -> bool:
    if not isinstance(observed, list) or not isinstance(expected, list):
        return False
    fields = {"fixture_id", "page", "start_offset", "end_offset", "quote_sha256"}
    return all(
        any(
            isinstance(item, dict) and all(item.get(key, _MISSING) == span[key] for key in fields)
            for item in observed
        )
        for span in expected
    )


_MISSING = object()


def _valid_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _contains(actual: Any, expected: Any) -> bool:
    if actual is _MISSING:
        return False
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value) for key, value in expected.items()
        )
    return bool(actual == expected)
