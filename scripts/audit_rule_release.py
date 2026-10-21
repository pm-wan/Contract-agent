"""Audit a development Rule/Skill/evaluation bundle; never publish it."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from contract_app.rules.release_audit import ReleaseAuditInvalid, build_development_release_audit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULE = ROOT / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
DEFAULT_SUITE = ROOT / "docs/development/06-rules/eval/legal.payment_conditions.cases.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", type=Path, default=DEFAULT_RULE)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--evaluation-report", type=Path)
    parser.add_argument(
        "--knowledge-manifest",
        type=Path,
        help="optionally bind local source bytes to separate legal and enterprise references",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the audit to a new file; existing files are never overwritten",
    )
    arguments = parser.parse_args()
    try:
        audit = build_development_release_audit(
            rule_path=arguments.rule,
            suite_path=arguments.suite,
            evaluation_report_path=arguments.evaluation_report,
            knowledge_manifest_path=arguments.knowledge_manifest,
        )
        serialized = json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if arguments.output is not None:
            _write_new_file(arguments.output, serialized)
    except (ReleaseAuditInvalid, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(serialized, end="")
    return 2 if not audit["release_ready"] else 0


def _write_new_file(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    except FileExistsError as exc:
        raise ReleaseAuditInvalid("release_audit_output_already_exists") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


if __name__ == "__main__":
    raise SystemExit(main())
