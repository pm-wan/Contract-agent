from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from contract_app.rules.compiler import load_development_rule
from contract_app.rules.evaluation import load_evaluation_suite, score_evaluation_suite
from contract_app.rules.release_audit import (
    ReleaseAuditInvalid,
    build_development_release_audit,
    verify_development_release_audit,
)

ROOT = Path(__file__).parents[1]
RULE_PATH = ROOT / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
SUITE_PATH = ROOT / "docs/development/06-rules/eval/legal.payment_conditions.cases.yaml"


def _write_bound_report(path: Path) -> dict[str, object]:
    rule = load_development_rule(RULE_PATH)
    suite = load_evaluation_suite(SUITE_PATH)
    report = score_evaluation_suite(suite, [], rule=rule)
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return report


def test_development_release_audit_binds_artifacts_but_never_grants_publication(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "payment-report.json"
    report = _write_bound_report(report_path)

    audit = build_development_release_audit(
        rule_path=RULE_PATH,
        suite_path=SUITE_PATH,
        evaluation_report_path=report_path,
    )

    assert audit["release_ready"] is False
    assert audit["gate_status"] == "blocked"
    assert audit["audit_version"] == "development-rule-release-audit.v2"
    assert audit["rule"]["source_sha256"] == report["suite"]["rule_sha256"]
    assert audit["skill"]["source_rule_sha256"] == audit["rule"]["source_sha256"]
    assert audit["evaluation"]["report_sha256"] == report["report_sha256"]
    assert audit["evaluation"]["metrics"] == report["metrics"]
    assert audit["evaluation"]["suite_source_sha256"] == report["suite"]["source_sha256"]
    assert (
        audit["evaluation"]["fixture_manifest_sha256"] == report["suite"]["fixture_manifest_sha256"]
    )
    assert audit["knowledge"]["normative"] == []
    assert audit["knowledge"]["enterprise_position"]
    assert all(item["content_sha256"] is None for item in audit["knowledge"]["enterprise_position"])
    assert "evaluation_thresholds_unapproved" in audit["blockers"]
    assert "publication_approval_missing" in audit["blockers"]
    assert "knowledge_source_content_unresolved" in audit["blockers"]
    assert (
        "dependent_check_completion_proof_unimplemented:legal.price.definition" in audit["blockers"]
    )


def test_development_release_audit_keeps_normative_and_enterprise_refs_separate(
    tmp_path: Path,
) -> None:
    rule_path = tmp_path / "rule-with-normative-ref.yaml"
    rule_path.write_text(
        RULE_PATH.read_text(encoding="utf-8").replace(
            "normative_refs: []", "normative_refs:\n  - legal.payment-terms/normative-v1"
        ),
        encoding="utf-8",
    )

    audit = build_development_release_audit(rule_path=rule_path, suite_path=SUITE_PATH)

    assert audit["knowledge"]["normative"] == [
        {
            "reference": "legal.payment-terms/normative-v1",
            "content_sha256": None,
            "status": "unresolved",
        }
    ]
    assert audit["knowledge"]["enterprise_position"] == [
        {
            "reference": "enterprise.payment-policy/mvp-v1",
            "content_sha256": None,
            "status": "unresolved",
        }
    ]


def test_development_release_audit_binds_knowledge_bytes_without_approving_them(
    tmp_path: Path,
) -> None:
    rule_path = tmp_path / "rule-with-normative-ref.yaml"
    rule_path.write_text(
        RULE_PATH.read_text(encoding="utf-8").replace(
            "normative_refs: []", "normative_refs:\n  - legal.payment-terms/normative-v1"
        ),
        encoding="utf-8",
    )
    legal_bytes = "开发审查材料：此文件不是经法务批准的正式依据。".encode()
    enterprise_bytes = "开发立场材料：此文件不是经业务批准的正式立场。".encode()
    (tmp_path / "normative.txt").write_bytes(legal_bytes)
    (tmp_path / "enterprise.txt").write_bytes(enterprise_bytes)
    manifest_path = tmp_path / "knowledge-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "development-knowledge-manifest.v1",
                "sources": [
                    {
                        "reference": "legal.payment-terms/normative-v1",
                        "kind": "normative",
                        "path": "normative.txt",
                    },
                    {
                        "reference": "enterprise.payment-policy/mvp-v1",
                        "kind": "enterprise_position",
                        "path": "enterprise.txt",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    audit = build_development_release_audit(
        rule_path=rule_path,
        suite_path=SUITE_PATH,
        knowledge_manifest_path=manifest_path,
    )

    assert audit["knowledge"]["manifest_sha256"] == sha256(manifest_path.read_bytes()).hexdigest()
    assert audit["knowledge"]["normative"] == [
        {
            "reference": "legal.payment-terms/normative-v1",
            "content_sha256": sha256(legal_bytes).hexdigest(),
            "status": "content_bound_unapproved",
        }
    ]
    assert audit["knowledge"]["enterprise_position"] == [
        {
            "reference": "enterprise.payment-policy/mvp-v1",
            "content_sha256": sha256(enterprise_bytes).hexdigest(),
            "status": "content_bound_unapproved",
        }
    ]
    assert "knowledge_source_content_unresolved" not in audit["blockers"]
    assert "knowledge_source_approval_missing" in audit["blockers"]
    assert audit["release_ready"] is False


def test_development_release_audit_rejects_knowledge_path_escape_and_wrong_kind(
    tmp_path: Path,
) -> None:
    rule_path = tmp_path / "rule-with-normative-ref.yaml"
    rule_path.write_text(
        RULE_PATH.read_text(encoding="utf-8").replace(
            "normative_refs: []", "normative_refs:\n  - legal.payment-terms/normative-v1"
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "knowledge-manifest.json"
    base = {
        "manifest_version": "development-knowledge-manifest.v1",
        "sources": [
            {
                "reference": "legal.payment-terms/normative-v1",
                "kind": "enterprise_position",
                "path": "../outside.txt",
            }
        ],
    }
    manifest_path.write_text(json.dumps(base), encoding="utf-8")

    with pytest.raises(
        ReleaseAuditInvalid,
        match="release_audit_knowledge_manifest_reference_invalid",
    ):
        build_development_release_audit(
            rule_path=rule_path,
            suite_path=SUITE_PATH,
            knowledge_manifest_path=manifest_path,
        )

    base["sources"][0]["kind"] = "normative"
    base["sources"][0]["path"] = "../outside.txt"
    manifest_path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(
        ReleaseAuditInvalid,
        match="release_audit_knowledge_manifest_path_invalid",
    ):
        build_development_release_audit(
            rule_path=rule_path,
            suite_path=SUITE_PATH,
            knowledge_manifest_path=manifest_path,
        )


def test_development_release_audit_cli_accepts_knowledge_manifest(tmp_path: Path) -> None:
    source_bytes = "开发态企业立场，占位内容，不是已批准政策。".encode()
    (tmp_path / "enterprise.md").write_bytes(source_bytes)
    manifest_path = tmp_path / "knowledge-manifest.json"
    manifest = {
        "manifest_version": "development-knowledge-manifest.v1",
        "sources": [
            {
                "reference": "enterprise.payment-policy/mvp-v1",
                "kind": "enterprise_position",
                "path": "enterprise.md",
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/audit_rule_release.py"),
            "--knowledge-manifest",
            str(manifest_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    audit = json.loads(result.stdout)
    assert (
        audit["knowledge"]["enterprise_position"][0]["content_sha256"]
        == sha256(source_bytes).hexdigest()
    )
    assert audit["knowledge"]["enterprise_position"][0]["status"] == "content_bound_unapproved"
    assert "knowledge_source_approval_missing" in audit["blockers"]
    assert audit["release_ready"] is False


def test_development_release_audit_rejects_knowledge_source_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("untrusted linked content", encoding="utf-8")
    source_dir = tmp_path / "manifest"
    source_dir.mkdir()
    (source_dir / "linked.md").symlink_to(outside)
    manifest_path = source_dir / "knowledge-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "development-knowledge-manifest.v1",
                "sources": [
                    {
                        "reference": "enterprise.payment-policy/mvp-v1",
                        "kind": "enterprise_position",
                        "path": "linked.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ReleaseAuditInvalid,
        match="release_audit_knowledge_manifest_symlink_forbidden",
    ):
        build_development_release_audit(
            rule_path=RULE_PATH,
            suite_path=SUITE_PATH,
            knowledge_manifest_path=manifest_path,
        )


def test_development_release_audit_requires_exact_evaluation_artifact_binding(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "payment-report.json"
    report = _write_bound_report(report_path)
    report["suite"]["rule_sha256"] = "0" * 64
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ReleaseAuditInvalid, match="release_audit_evaluation_report_invalid"):
        build_development_release_audit(
            rule_path=RULE_PATH,
            suite_path=SUITE_PATH,
            evaluation_report_path=report_path,
        )


def test_development_release_audit_rejects_a_valid_report_for_other_artifacts(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "payment-report.json"
    _write_bound_report(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["suite"]["source_sha256"] = "0" * 64
    report_without_hash = dict(report)
    report_without_hash.pop("report_sha256")
    report["report_sha256"] = sha256(
        json.dumps(
            report_without_hash,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ReleaseAuditInvalid, match="release_audit_evaluation_artifact_mismatch"):
        build_development_release_audit(
            rule_path=RULE_PATH,
            suite_path=SUITE_PATH,
            evaluation_report_path=report_path,
        )


def test_development_release_audit_rejects_report_with_replaced_case_id(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "payment-report.json"
    _write_bound_report(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["cases"][0]["case_id"] = "unrelated-case-001"
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
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ReleaseAuditInvalid, match="release_audit_evaluation_artifact_mismatch"):
        build_development_release_audit(
            rule_path=RULE_PATH,
            suite_path=SUITE_PATH,
            evaluation_report_path=report_path,
        )


def test_development_release_audit_rejects_reclassified_suite_case(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "payment-report.json"
    report = _write_bound_report(report_path)
    report["cases"][0]["kind"] = "negative"
    report["metrics"]["by_case_kind"].pop("positive")
    report["metrics"]["by_case_kind"]["negative"].update(
        case_count=2,
        failed_case_count=2,
        case_pass_rate=0.0,
    )
    report["metrics"]["missing_case_kinds"] = ["boundary", "positive", "security"]
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
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ReleaseAuditInvalid, match="release_audit_evaluation_artifact_mismatch"):
        build_development_release_audit(
            rule_path=RULE_PATH,
            suite_path=SUITE_PATH,
            evaluation_report_path=report_path,
        )


def test_development_release_audit_accepts_legacy_report_with_matching_case_ids(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "payment-report.json"
    report = _write_bound_report(report_path)
    report["report_version"] = "1.0"
    report["metrics"].pop("by_case_kind")
    report["metrics"].pop("missing_case_kinds")
    for case in report["cases"]:
        case.pop("kind")
        case.pop("observed")
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
    report_path.write_text(json.dumps(report), encoding="utf-8")

    audit = build_development_release_audit(
        rule_path=RULE_PATH,
        suite_path=SUITE_PATH,
        evaluation_report_path=report_path,
    )

    assert audit["evaluation"]["report_sha256"] == report["report_sha256"]
    assert audit["release_ready"] is False


def test_development_release_audit_cli_reports_blocked_and_does_not_overwrite(
    tmp_path: Path,
) -> None:
    output = tmp_path / "audit.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/audit_rule_release.py"),
        "--output",
        str(output),
    ]

    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    assert result.returncode == 2
    parsed = json.loads(result.stdout)
    assert parsed["gate_status"] == "blocked"
    assert parsed["release_ready"] is False
    assert output.stat().st_mode & 0o777 == 0o600

    second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert second.returncode == 1
    assert json.loads(second.stderr) == {"error": "release_audit_output_already_exists"}


def test_development_release_audit_reports_missing_evaluation_report() -> None:
    audit = build_development_release_audit(rule_path=RULE_PATH, suite_path=SUITE_PATH)

    assert audit["evaluation"]["report_sha256"] is None
    assert audit["evaluation"]["gate_status"] == "not_run"
    assert "evaluation_report_missing" in audit["blockers"]
    assert audit["release_ready"] is False


def test_development_release_audit_verifier_accepts_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "payment-report.json"
    _write_bound_report(report_path)
    audit = build_development_release_audit(
        rule_path=RULE_PATH,
        suite_path=SUITE_PATH,
        evaluation_report_path=report_path,
    )

    assert verify_development_release_audit(audit) == audit
    tampered = {**audit, "audit_sha256": "0" * 64}
    with pytest.raises(ReleaseAuditInvalid, match="release_audit_digest_mismatch"):
        verify_development_release_audit(tampered)

    inconsistent_metrics = deepcopy(audit)
    inconsistent_metrics["evaluation"]["metrics"]["by_case_kind"]["positive"].update(
        observation_count=1,
        case_pass_rate=1.0,
        passed_case_count=1,
        failed_case_count=0,
    )
    unsigned = {key: value for key, value in inconsistent_metrics.items() if key != "audit_sha256"}
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    inconsistent_metrics["audit_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    with pytest.raises(ReleaseAuditInvalid, match="release_audit_metrics_invalid"):
        verify_development_release_audit(inconsistent_metrics)

    inconsistent_coverage = deepcopy(audit)
    inconsistent_coverage["evaluation"]["metrics"]["missing_case_kinds"].remove("security")
    unsigned = {key: value for key, value in inconsistent_coverage.items() if key != "audit_sha256"}
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    inconsistent_coverage["audit_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    with pytest.raises(ReleaseAuditInvalid, match="release_audit_metrics_invalid"):
        verify_development_release_audit(inconsistent_coverage)
