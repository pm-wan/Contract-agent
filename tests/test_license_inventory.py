from __future__ import annotations

import importlib.util
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Any

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts/export_license_inventory.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("export_license_inventory", _SCRIPT_PATH)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = _SCRIPT_MODULE
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)
build_inventory = _SCRIPT_MODULE.build_inventory
inventory_matches_snapshot = _SCRIPT_MODULE.inventory_matches_snapshot


class FakeMetadata:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str) -> Any:
        return self._values.get(key)

    def get_all(self, key: str, default: list[str] | None = None) -> list[str]:
        return self._values.get(key, default or [])


class FakeDistribution:
    version = "1.2.3"
    metadata = FakeMetadata(
        {
            "License-Expression": "BSD-3-Clause",
            "Classifier": [
                "License :: OSI Approved :: BSD License",
                "Programming Language :: Python",
            ],
        }
    )
    files = [
        Path("sample_pkg-1.2.3.dist-info/METADATA"),
        Path("sample_pkg-1.2.3.dist-info/licenses/LICENSE"),
    ]


def test_inventory_is_lock_bound_and_marks_license_metadata_unreviewed(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n[[package]]\nname = "sample-pkg"\nversion = "1.2.3"\n'
        'source = { registry = "https://example.invalid/simple" }\n',
        encoding="utf-8",
    )

    report = build_inventory(lock, get_distribution=lambda _name: FakeDistribution())

    assert report["package_count"] == 1
    assert report["lock_sha256"]
    assert report["legal_review_status"] == "unreviewed"
    package = report["packages"][0]
    assert package["source_kind"] == "registry"
    assert package["license_expression"] == "BSD-3-Clause"
    assert package["license_classifiers"] == ["OSI Approved :: BSD License"]
    assert package["license_files"] == ["sample_pkg-1.2.3.dist-info/licenses/LICENSE"]
    assert package["review_status"] == "unreviewed"


def test_inventory_reports_locked_packages_absent_from_this_environment(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n[[package]]\nname = "sample-pkg"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )

    def missing_distribution(_name: str) -> FakeDistribution:
        raise PackageNotFoundError("sample-pkg")

    report = build_inventory(lock, get_distribution=missing_distribution)

    assert report["package_count"] == 0
    assert report["not_installed_locked_package_count"] == 1
    assert report["not_installed_locked_packages"] == [
        {"name": "sample-pkg", "version": "1.2.3", "source_kind": "unknown"}
    ]


def test_inventory_snapshot_match_ignores_only_python_patch_level() -> None:
    baseline = {
        "python_version": "3.13.13",
        "platform": "linux",
        "lock_sha256": "locked-hash",
        "packages": [{"name": "sample-pkg", "license_expression": "BSD-3-Clause"}],
    }
    current = {**baseline, "python_version": "3.13.14"}

    assert inventory_matches_snapshot(current, baseline)
    assert not inventory_matches_snapshot({**current, "lock_sha256": "changed-hash"}, baseline)
    assert not inventory_matches_snapshot({**current, "platform": "win32"}, baseline)
    assert not inventory_matches_snapshot({**current, "python_version": "3.14.0"}, baseline)
    assert not inventory_matches_snapshot(current, {**baseline, "python_version": "invalid"})
