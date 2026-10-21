"""Export the running FastAPI application's canonical OpenAPI YAML document."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from contract_app.api import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs/development/04-interfaces/OpenAPI.yaml"


def render() -> str:
    return yaml.safe_dump(app.openapi(), allow_unicode=True, sort_keys=False, width=100)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the checked-in OpenAPI document differs from the running application",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    generated = render()
    if arguments.check:
        try:
            current = arguments.output.read_text(encoding="utf-8")
        except OSError:
            print(f"OpenAPI artifact missing: {arguments.output}", file=sys.stderr)
            return 1
        if current != generated:
            print(
                "OpenAPI artifact is stale: regenerate with "
                "`uv run python scripts/export_openapi.py > "
                "docs/development/04-interfaces/OpenAPI.yaml`",
                file=sys.stderr,
            )
            return 1
        return 0
    sys.stdout.write(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
