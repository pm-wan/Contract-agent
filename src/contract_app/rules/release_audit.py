"""Content-bind development rule release inputs without granting publication."""

from __future__ import annotations

import json
import math
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from contract_app.bounded_files import read_bounded_file
from contract_app.rules.compiler import load_development_rule_bytes
from contract_app.rules.evaluation import (
    EVALUATION_CASE_KINDS,
    MAX_EVALUATION_REPORT_BYTES,
    EvaluationDataInvalid,
    load_evaluation_suite,
    verify_evaluation_report,
)
from contract_app.rules.knowledge_sources import (
    KnowledgeManifestInvalid,
    load_development_knowledge_manifest,
)
from contract_app.rules.skill_package import render_development_skill
from contract_app.workflow.definitions import current_tool_schema
from contract_app.workflow.output_contract import candidate_output_schema

MAX_RELEASE_AUDIT_INPUT_BYTES = 2 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_AUDIT_METRIC_FIELDS = frozenset(
    {
        "case_count",
        "observation_count",
        "passed_case_count",
        "failed_case_count",
        "case_pass_rate",
    }
)
_AUDIT_FIELDS = frozenset(
    {
        "audit_version",
        "release_ready",
        "gate_status",
        "rule",
        "knowledge",
        "skill",
        "evaluation",
        "blockers",
        "audit_sha256",
    }
)


class ReleaseAuditInvalid(ValueError):
    """A development release audit input is invalid or refers to other artifacts."""


def build_development_release_audit(
    *,
    rule_path: Path,
    suite_path: Path,
    evaluation_report_path: Path | None = None,
    knowledge_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Return a deterministic, permanently non-publishing audit of M2 artifacts."""
    rule_source = _read_bounded(rule_path, "rule", MAX_RELEASE_AUDIT_INPUT_BYTES)
    rule = load_development_rule_bytes(rule_source)
    suite = load_evaluation_suite(suite_path)
    if (
        suite.rule_ref != f"{rule.rule_id}@{rule.version}"
        or suite.reference not in rule.eval_suite_refs
    ):
        raise ReleaseAuditInvalid("release_audit_suite_rule_mismatch")

    tool_contract = current_tool_schema()
    output_schema = candidate_output_schema()
    skill = render_development_skill(
        rule_source,
        tool_contract=tool_contract,
        output_schema=output_schema,
    )

    knowledge_manifest_sha256: str | None = None
    knowledge_content: dict[str, str] = {}
    if knowledge_manifest_path is not None:
        try:
            knowledge_manifest = load_development_knowledge_manifest(
                knowledge_manifest_path,
                normative_refs=rule.normative_refs,
                enterprise_position_refs=rule.enterprise_position_refs,
            )
        except KnowledgeManifestInvalid as exc:
            raise ReleaseAuditInvalid(f"release_audit_{exc}") from exc
        knowledge_manifest_sha256 = knowledge_manifest.source_sha256
        knowledge_content = {
            binding.reference: binding.content_sha256 for binding in knowledge_manifest.bindings
        }

    report: dict[str, Any] | None = None
    if evaluation_report_path is not None:
        report_bytes = _read_bounded(
            evaluation_report_path, "evaluation_report", MAX_EVALUATION_REPORT_BYTES
        )
        report_value = _load_json_object(report_bytes)
        try:
            report = verify_evaluation_report(report_value)
        except EvaluationDataInvalid as exc:
            raise ReleaseAuditInvalid("release_audit_evaluation_report_invalid") from exc
        report_suite = report["suite"]
        if (
            report_suite["suite_id"] != suite.suite_id
            or report_suite["version"] != suite.version
            or report_suite["rule_ref"] != suite.rule_ref
            or report_suite["level"] != suite.level
            or report_suite["rule_sha256"] != rule.source_sha256
            or report_suite["source_sha256"] != suite.source_sha256
            or report_suite["fixture_manifest_sha256"] != suite.fixture_manifest_sha256
        ):
            raise ReleaseAuditInvalid("release_audit_evaluation_artifact_mismatch")
        expected_case_kinds = {case["case_id"]: case["kind"] for case in suite.cases}
        reported_case_kinds = {case["case_id"]: case.get("kind") for case in report["cases"]}
        if set(reported_case_kinds) != set(expected_case_kinds) or (
            report["report_version"] in ("1.1", "1.2")
            and reported_case_kinds != expected_case_kinds
        ):
            raise ReleaseAuditInvalid("release_audit_evaluation_artifact_mismatch")

    blockers = set(rule.publication_blockers)
    blockers.update(skill.publication_blockers)
    blockers.discard("knowledge_source_content_unresolved")
    knowledge_content_complete = bool(
        rule.normative_refs and rule.enterprise_position_refs
    ) and all(
        reference in knowledge_content
        for reference in (*rule.normative_refs, *rule.enterprise_position_refs)
    )
    if knowledge_content_complete:
        blockers.add("knowledge_source_approval_missing")
    else:
        blockers.add("knowledge_source_content_unresolved")
        blockers.add("knowledge_source_approval_missing")
    if report is None:
        blockers.add("evaluation_report_missing")
        evaluation_artifact: dict[str, Any] = {
            "report_sha256": None,
            "observations_sha256": None,
            "metrics": None,
            "gate_status": "not_run",
        }
    else:
        report_metrics = report["metrics"]
        if report_metrics["failed_case_count"] > 0:
            blockers.add("evaluation_cases_failed")
        if report["gate_status"] != "passed":
            blockers.add("evaluation_thresholds_unapproved")
        evaluation_artifact = {
            "report_sha256": report["report_sha256"],
            "observations_sha256": report["observations_sha256"],
            "metrics": report_metrics,
            "gate_status": report["gate_status"],
        }

    audit: dict[str, Any] = {
        "audit_version": "development-rule-release-audit.v2",
        "release_ready": False,
        "gate_status": "blocked",
        "rule": {
            "rule_id": rule.rule_id,
            "version": rule.version,
            "source_sha256": rule.source_sha256,
        },
        "knowledge": {
            "manifest_sha256": knowledge_manifest_sha256,
            "normative": [
                _knowledge_reference(reference, knowledge_content)
                for reference in rule.normative_refs
            ],
            "enterprise_position": [
                _knowledge_reference(reference, knowledge_content)
                for reference in rule.enterprise_position_refs
            ],
        },
        "skill": {
            "status": skill.status,
            "generator_version": skill.generator_version,
            "source_rule_sha256": skill.rule_sha256,
            "tool_contract_sha256": skill.tool_contract_sha256,
            "output_schema_sha256": skill.output_schema_sha256,
            "content_sha256": skill.skill_sha256,
        },
        "evaluation": {
            "suite_id": suite.suite_id,
            "suite_version": suite.version,
            "suite_reference": suite.reference,
            "suite_source_sha256": suite.source_sha256,
            "fixture_manifest_sha256": suite.fixture_manifest_sha256,
            **evaluation_artifact,
        },
        "blockers": sorted(blockers),
    }
    audit["audit_sha256"] = _canonical_sha256(audit)
    verify_development_release_audit(audit)
    return audit


def verify_development_release_audit(value: Any) -> dict[str, Any]:
    """Validate an audit artifact while preserving its permanently blocked state."""
    if not isinstance(value, dict) or value.keys() != _AUDIT_FIELDS:
        raise ReleaseAuditInvalid("release_audit_shape_invalid")
    if (
        value.get("audit_version") != "development-rule-release-audit.v2"
        or value.get("release_ready") is not False
        or value.get("gate_status") != "blocked"
    ):
        raise ReleaseAuditInvalid("release_audit_metadata_invalid")

    rule = value.get("rule")
    if (
        not isinstance(rule, dict)
        or rule.keys() != {"rule_id", "version", "source_sha256"}
        or not _nonempty(rule.get("rule_id"))
        or not _nonempty(rule.get("version"))
        or not _is_sha256(rule.get("source_sha256"))
    ):
        raise ReleaseAuditInvalid("release_audit_rule_invalid")

    knowledge = value.get("knowledge")
    if not isinstance(knowledge, dict) or knowledge.keys() != {
        "manifest_sha256",
        "normative",
        "enterprise_position",
    }:
        raise ReleaseAuditInvalid("release_audit_knowledge_invalid")
    manifest_sha256 = knowledge.get("manifest_sha256")
    if manifest_sha256 is not None and not _is_sha256(manifest_sha256):
        raise ReleaseAuditInvalid("release_audit_knowledge_invalid")
    has_bound_content = False
    has_unresolved_content = False
    for category in ("normative", "enterprise_position"):
        references = knowledge[category]
        if not isinstance(references, list):
            raise ReleaseAuditInvalid("release_audit_knowledge_invalid")
        for reference in references:
            if (
                not isinstance(reference, dict)
                or reference.keys() != {"reference", "content_sha256", "status"}
                or not _nonempty(reference.get("reference"))
            ):
                raise ReleaseAuditInvalid("release_audit_knowledge_invalid")
            content_sha256 = reference.get("content_sha256")
            status = reference.get("status")
            if content_sha256 is None and status == "unresolved":
                has_unresolved_content = True
            elif _is_sha256(content_sha256) and status == "content_bound_unapproved":
                has_bound_content = True
            else:
                raise ReleaseAuditInvalid("release_audit_knowledge_invalid")
    if has_bound_content and manifest_sha256 is None:
        raise ReleaseAuditInvalid("release_audit_knowledge_invalid")

    skill = value.get("skill")
    if (
        not isinstance(skill, dict)
        or skill.keys()
        != {
            "status",
            "generator_version",
            "source_rule_sha256",
            "tool_contract_sha256",
            "output_schema_sha256",
            "content_sha256",
        }
        or skill.get("status") != "development_candidate"
        or not _nonempty(skill.get("generator_version"))
        or skill.get("source_rule_sha256") != rule["source_sha256"]
        or any(
            not _is_sha256(skill.get(field))
            for field in (
                "source_rule_sha256",
                "tool_contract_sha256",
                "output_schema_sha256",
                "content_sha256",
            )
        )
    ):
        raise ReleaseAuditInvalid("release_audit_skill_invalid")

    evaluation = value.get("evaluation")
    if not isinstance(evaluation, dict) or evaluation.keys() != {
        "suite_id",
        "suite_version",
        "suite_reference",
        "suite_source_sha256",
        "fixture_manifest_sha256",
        "report_sha256",
        "observations_sha256",
        "metrics",
        "gate_status",
    }:
        raise ReleaseAuditInvalid("release_audit_evaluation_invalid")
    if (
        not _nonempty(evaluation.get("suite_id"))
        or not _nonempty(evaluation.get("suite_version"))
        or evaluation.get("suite_reference")
        != f"{evaluation.get('suite_id')}@{evaluation.get('suite_version')}"
        or not _is_sha256(evaluation.get("suite_source_sha256"))
        or not _is_sha256(evaluation.get("fixture_manifest_sha256"))
    ):
        raise ReleaseAuditInvalid("release_audit_evaluation_invalid")
    report_sha256 = evaluation.get("report_sha256")
    if report_sha256 is None:
        if (
            evaluation.get("observations_sha256") is not None
            or evaluation.get("metrics") is not None
            or evaluation.get("gate_status") != "not_run"
        ):
            raise ReleaseAuditInvalid("release_audit_evaluation_invalid")
    else:
        if (
            not _is_sha256(report_sha256)
            or not _is_sha256(evaluation.get("observations_sha256"))
            or evaluation.get("gate_status") != "blocked_no_approved_thresholds"
        ):
            raise ReleaseAuditInvalid("release_audit_evaluation_invalid")
        _validate_audit_metrics(evaluation.get("metrics"))

    blockers = value.get("blockers")
    if (
        not isinstance(blockers, list)
        or not blockers
        or any(not _nonempty(blocker) for blocker in blockers)
        or blockers != sorted(set(blockers))
    ):
        raise ReleaseAuditInvalid("release_audit_blockers_invalid")
    if "knowledge_source_approval_missing" not in blockers or (
        has_unresolved_content or not knowledge["normative"] or not knowledge["enterprise_position"]
    ) != ("knowledge_source_content_unresolved" in blockers):
        raise ReleaseAuditInvalid("release_audit_blockers_invalid")
    claimed_digest = value.get("audit_sha256")
    if not _is_sha256(claimed_digest):
        raise ReleaseAuditInvalid("release_audit_digest_invalid")
    unsigned = dict(value)
    unsigned.pop("audit_sha256")
    if _canonical_sha256(unsigned) != claimed_digest:
        raise ReleaseAuditInvalid("release_audit_digest_mismatch")
    return value


def _validate_audit_metrics(value: Any) -> None:
    metric_fields = (
        _AUDIT_METRIC_FIELDS,
        _AUDIT_METRIC_FIELDS | {"by_case_kind"},
        _AUDIT_METRIC_FIELDS | {"by_case_kind", "missing_case_kinds"},
    )
    if not isinstance(value, dict) or frozenset(value) not in metric_fields:
        raise ReleaseAuditInvalid("release_audit_metrics_invalid")
    overall_counts = _validate_audit_metric_summary(
        {field: value[field] for field in _AUDIT_METRIC_FIELDS}
    )
    if "by_case_kind" not in value:
        return

    by_case_kind = value.get("by_case_kind")
    if (
        not isinstance(by_case_kind, dict)
        or not by_case_kind
        or by_case_kind.keys() - EVALUATION_CASE_KINDS
    ):
        raise ReleaseAuditInvalid("release_audit_metrics_invalid")
    grouped_counts = [_validate_audit_metric_summary(summary) for summary in by_case_kind.values()]
    grouped_totals = tuple(sum(counts[index] for counts in grouped_counts) for index in range(4))
    if grouped_totals != overall_counts:
        raise ReleaseAuditInvalid("release_audit_metrics_invalid")
    if "missing_case_kinds" in value:
        missing_case_kinds = value.get("missing_case_kinds")
        if (
            not isinstance(missing_case_kinds, list)
            or any(not isinstance(case_kind, str) for case_kind in missing_case_kinds)
            or missing_case_kinds != sorted(set(missing_case_kinds))
            or set(missing_case_kinds) != EVALUATION_CASE_KINDS - by_case_kind.keys()
        ):
            raise ReleaseAuditInvalid("release_audit_metrics_invalid")


def _validate_audit_metric_summary(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, dict) or value.keys() != _AUDIT_METRIC_FIELDS:
        raise ReleaseAuditInvalid("release_audit_metrics_invalid")
    counts = tuple(
        value.get(field)
        for field in (
            "case_count",
            "observation_count",
            "passed_case_count",
            "failed_case_count",
        )
    )
    if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts):
        raise ReleaseAuditInvalid("release_audit_metrics_invalid")
    case_count, observation_count, passed, failed = (cast(int, count) for count in counts)
    rate = value.get("case_pass_rate")
    if (
        case_count < 1
        or observation_count > case_count
        or passed + failed != case_count
        or not isinstance(rate, (int, float))
        or isinstance(rate, bool)
        or rate < 0
        or rate > 1
        or not math.isfinite(rate)
        or rate != passed / case_count
    ):
        raise ReleaseAuditInvalid("release_audit_metrics_invalid")
    return case_count, observation_count, passed, failed


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _knowledge_reference(reference: str, content: dict[str, str]) -> dict[str, Any]:
    content_sha256 = content.get(reference)
    return {
        "reference": reference,
        "content_sha256": content_sha256,
        "status": "content_bound_unapproved" if content_sha256 else "unresolved",
    }


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _read_bounded(path: Path, label: str, maximum: int) -> bytes:
    try:
        content = read_bounded_file(path, maximum)
    except OSError as exc:
        raise ReleaseAuditInvalid(f"release_audit_{label}_unavailable") from exc
    if len(content) > maximum:
        raise ReleaseAuditInvalid(f"release_audit_{label}_too_large")
    return content


def _load_json_object(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonstandard_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReleaseAuditInvalid("release_audit_evaluation_report_json_invalid") from exc
    if not isinstance(value, dict):
        raise ReleaseAuditInvalid("release_audit_evaluation_report_shape_invalid")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> Any:
    raise ValueError(f"nonstandard_json_constant:{value}")


def _canonical_sha256(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
