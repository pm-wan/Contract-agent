"""Inspect the development RuleDefinition dependency graph without executing rules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from contract_app.rules.dependency_graph import load_rule_execution_graph

ROOT = Path(__file__).resolve().parents[1]
RULE_DIRECTORY = ROOT / "docs/development/06-rules/rules"


def main() -> int:
    try:
        graph = load_rule_execution_graph(RULE_DIRECTORY)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    report = graph.as_manifest()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 2 if graph.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
