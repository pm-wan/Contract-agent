"""Export locked package license metadata without making licensing judgments."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tomllib
from collections.abc import Callable, Iterable
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

_LICENSE_FILE_PREFIXES = ("license", "licence", "copying", "notice", "copyright")


def _source_kind(source: Any) -> str:
    if not isinstance(source, dict) or not source:
        return "unknown"
    return next(iter(source))


def _license_file_paths(files: Iterable[Any] | None) -> list[str]:
    if files is None:
        return []
    paths = {
        str(path)
        for path in files
        if Path(str(path)).name.casefold().startswith(_LICENSE_FILE_PREFIXES)
    }
    return sorted(paths, key=str.casefold)


def _package_record(package: dict[str, Any], dist: Distribution) -> dict[str, Any]:
    metadata = dist.metadata
    license_expression = metadata.get("License-Expression")
    license_text = metadata.get("License")
    license_classifiers = sorted(
        {
            value.removeprefix("License :: ")
            for value in metadata.get_all("Classifier", [])
            if value.startswith("License :: ")
        },
        key=str.casefold,
    )
    return {
        "name": package["name"],
        "version": package["version"],
        "source_kind": _source_kind(package.get("source")),
        "license_expression": license_expression,
        "license_text": license_text,
        "license_classifiers": license_classifiers,
        "license_files": _license_file_paths(dist.files),
        "review_status": "unreviewed",
    }


def build_inventory(
    lock_path: Path,
    *,
    get_distribution: Callable[[str], Distribution] = distribution,
) -> dict[str, Any]:
    lock_bytes = lock_path.read_bytes()
    try:
        lock = tomllib.loads(lock_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("license_inventory_lock_invalid") from exc
    packages = lock.get("package")
    if not isinstance(packages, list) or not packages:
        raise ValueError("license_inventory_lock_packages_missing")

    records: list[dict[str, Any]] = []
    not_installed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for package in packages:
        if (
            not isinstance(package, dict)
            or not isinstance(package.get("name"), str)
            or not isinstance(package.get("version"), str)
        ):
            raise ValueError("license_inventory_lock_package_invalid")
        identity = (package["name"].casefold().replace("_", "-"), package["version"])
        if identity in seen:
            continue
        seen.add(identity)
        try:
            dist = get_distribution(package["name"])
        except PackageNotFoundError:
            not_installed.append(
                {
                    "name": package["name"],
                    "version": package["version"],
                    "source_kind": _source_kind(package.get("source")),
                }
            )
            continue
        if dist.version != package["version"]:
            raise ValueError(f"license_inventory_package_version_mismatch:{package['name']}")
        records.append(_package_record(package, dist))

    records.sort(key=lambda item: (item["name"].casefold(), item["version"]))
    return {
        "inventory_version": "1",
        "inventory_scope": "installed_distributions_for_current_environment",
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "locked_package_count": len(seen),
        "package_count": len(records),
        "not_installed_locked_package_count": len(not_installed),
        "not_installed_locked_packages": sorted(
            not_installed, key=lambda item: (item["name"].casefold(), item["version"])
        ),
        "legal_review_status": "unreviewed",
        "packages": records,
    }


def _python_major_minor(version: str) -> str | None:
    parts = version.split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
        return None
    return ".".join(parts[:2])


def inventory_matches_snapshot(inventory: dict[str, Any], snapshot: Any) -> bool:
    """Compare a generated inventory with its baseline, ignoring Python patch level."""
    if not isinstance(snapshot, dict):
        return False
    current = dict(inventory)
    expected = dict(snapshot)
    current_python = _python_major_minor(current.get("python_version", ""))
    expected_python = _python_major_minor(expected.get("python_version", ""))
    if current_python is None or expected_python is None:
        return False
    current["python_version"] = current_python
    expected["python_version"] = expected_python
    return current == expected


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("license_inventory_snapshot_duplicate_key")
        result[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock", type=Path, default=Path(__file__).resolve().parents[1] / "uv.lock"
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--output", type=Path)
    output_group.add_argument("--check", type=Path)
    arguments = parser.parse_args()
    try:
        report = build_inventory(arguments.lock)
        if arguments.check is not None:
            snapshot = json.loads(
                arguments.check.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
            )
            if not inventory_matches_snapshot(report, snapshot):
                raise ValueError("license_inventory_snapshot_mismatch")
            print("license_inventory_snapshot_matches")
        else:
            content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if arguments.output is None:
                sys.stdout.write(content)
            else:
                with arguments.output.open("x", encoding="utf-8") as output:
                    output.write(content)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
