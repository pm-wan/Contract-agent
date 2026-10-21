"""Verify the integrity and internal consistency of a persisted evaluation report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from contract_app.rules.evaluation import (
    MAX_EVALUATION_REPORT_BYTES,
    EvaluationDataInvalid,
    read_limited_evaluation_file,
    verify_evaluation_report,
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationDataInvalid("evaluation_report_duplicate_json_key")
        result[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    arguments = parser.parse_args()
    try:
        report = json.loads(
            read_limited_evaluation_file(
                arguments.report,
                MAX_EVALUATION_REPORT_BYTES,
                too_large="evaluation_report_too_large",
                unavailable="evaluation_report_unavailable",
            ).decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
        verified = verify_evaluation_report(report)
    except (EvaluationDataInvalid, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "verified": True,
                "report_sha256": verified["report_sha256"],
                "suite_id": verified["suite"]["suite_id"],
                "case_count": verified["metrics"]["case_count"],
                "missing_case_kinds": verified["metrics"].get("missing_case_kinds", []),
                "gate_status": verified["gate_status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
