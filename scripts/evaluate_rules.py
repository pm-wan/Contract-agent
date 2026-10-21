"""Score supplied observations against a development evaluation suite.

This command reports measurements but deliberately cannot pass the release gate
until QA/domain owners provide approved thresholds.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from contract_app.rules.compiler import RuleValidationError, load_development_rule
from contract_app.rules.evaluation import (
    MAX_EVALUATION_CASES,
    MAX_EVALUATION_OBSERVATION_LINE_BYTES,
    MAX_EVALUATION_OBSERVATIONS_BYTES,
    EvaluationDataInvalid,
    load_evaluation_suite,
    read_limited_evaluation_file,
    render_evaluation_report_markdown,
    score_evaluation_suite,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "docs/development/06-rules/eval/legal.payment_conditions.cases.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--rule", type=Path, required=True)
    parser.add_argument(
        "--observations",
        type=Path,
        required=True,
        help="JSONL produced by a separately executed, reviewed evaluation run",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="write the deterministic report to a new file (existing files are never overwritten)",
    )
    parser.add_argument(
        "--markdown-report",
        type=Path,
        help="write a Chinese-readable summary to a new file",
    )
    arguments = parser.parse_args()
    try:
        suite = load_evaluation_suite(arguments.suite)
        rule = load_development_rule(arguments.rule)
        if suite.rule_ref != f"{rule.rule_id}@{rule.version}":
            raise EvaluationDataInvalid("evaluation_suite_rule_reference_mismatch")
        observations_bytes = read_limited_evaluation_file(
            arguments.observations,
            MAX_EVALUATION_OBSERVATIONS_BYTES,
            too_large="evaluation_observations_too_large",
            unavailable="evaluation_observations_unavailable",
        )
        observations: list[dict[str, Any]] = []
        for line_number, raw_line in enumerate(BytesIO(observations_bytes), start=1):
            if len(raw_line) > MAX_EVALUATION_OBSERVATION_LINE_BYTES:
                raise EvaluationDataInvalid(f"evaluation_observation_line_too_large:{line_number}")
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise EvaluationDataInvalid(
                    f"evaluation_observation_encoding_invalid:{line_number}"
                ) from exc
            if not line.strip():
                continue
            try:
                value = json.loads(
                    line,
                    object_pairs_hook=_unique_json_object,
                    parse_constant=_reject_nonstandard_json_number,
                )
            except EvaluationDataInvalid as exc:
                raise EvaluationDataInvalid(f"{exc}:{line_number}") from exc
            except json.JSONDecodeError as exc:
                raise EvaluationDataInvalid(f"observation_json_invalid:{line_number}") from exc
            if not isinstance(value, dict):
                raise EvaluationDataInvalid(f"observation_shape_invalid:{line_number}")
            if len(observations) >= MAX_EVALUATION_CASES:
                raise EvaluationDataInvalid("evaluation_observations_too_many")
            observations.append(value)
        report = score_evaluation_suite(suite, observations, rule=rule)
        if (
            arguments.report is not None
            and arguments.markdown_report is not None
            and arguments.report.resolve() == arguments.markdown_report.resolve()
        ):
            raise EvaluationDataInvalid("evaluation_report_paths_must_differ")
        if arguments.report is not None:
            _write_report(arguments.report, report)
        if arguments.markdown_report is not None:
            _write_markdown_report(arguments.markdown_report, report)
    except (EvaluationDataInvalid, RuleValidationError, OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if report["gate_status"] != "passed" else 0


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EvaluationDataInvalid("evaluation_observation_duplicate_json_key")
        value[key] = item
    return value


def _reject_nonstandard_json_number(value: str) -> Any:
    raise EvaluationDataInvalid(f"evaluation_observation_nonstandard_number:{value}")


def _write_report(path: Path, report: dict[str, Any]) -> None:
    content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    except FileExistsError as exc:
        raise EvaluationDataInvalid("evaluation_report_already_exists") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _write_markdown_report(path: Path, report: dict[str, Any]) -> None:
    content = render_evaluation_report_markdown(report)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    except FileExistsError as exc:
        raise EvaluationDataInvalid("evaluation_markdown_report_already_exists") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


if __name__ == "__main__":
    raise SystemExit(main())
