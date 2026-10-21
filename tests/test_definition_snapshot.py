import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from contract_app.workflow.definitions import (
    GENERATED_SKILL_SOURCE_PATH,
    RULE_RELATIVE_PATH,
    DefinitionSnapshotInvalid,
    FrozenDefinitionSnapshot,
)
from contract_app.workflow.model_profile import capture_model_profile, model_profile_digest


def test_captured_definition_sources_round_trip_after_source_files_change(tmp_path: Path) -> None:
    root = tmp_path
    rule_path = root / RULE_RELATIVE_PATH
    rule_path.parent.mkdir(parents=True)
    repository = Path(__file__).parents[1]
    rule_path.write_bytes((repository / RULE_RELATIVE_PATH).read_bytes())
    reference_skill = root / "skills/cn-procurement-contract-audit/SKILL.md"
    reference_skill.parent.mkdir(parents=True)
    reference_skill.write_bytes(
        (repository / "skills/cn-procurement-contract-audit/SKILL.md").read_bytes()
    )

    captured = FrozenDefinitionSnapshot.capture(root)
    rule_path.write_text("changed after queueing", encoding="utf-8")
    reference_skill.write_text("changed after queueing", encoding="utf-8")

    restored = FrozenDefinitionSnapshot.from_storage(captured.storage_value())
    assert restored == captured
    assert restored.manifest["knowledge_sources"] == {
        "legal_normative_refs": [],
        "enterprise_position_refs": ["enterprise.payment-policy/mvp-v1"],
        "content_binding": "unresolved_development_references",
    }
    assert restored.manifest["dependency_graph"]["status"] == "blocked"
    assert restored.manifest["dependency_graph"]["blockers"] == [
        "dependent_check_definition_missing:legal.price.definition"
    ]
    assert restored.manifest["dependency_graph"]["execution_order"] == []
    assert restored.manifest["dependency_graph"]["is_execution_proof"] is False
    assert restored.manifest["dependency_graph"]["is_approval_record"] is False
    assert (
        restored.manifest["rule"]["source_sha256"]
        == sha256((repository / RULE_RELATIVE_PATH).read_bytes()).hexdigest()
    )
    assert restored.skill_source.startswith("<!-- development-only:")
    assert "full_review" not in restored.skill_source
    assert restored.manifest["skill"]["source_path"] == GENERATED_SKILL_SOURCE_PATH
    assert restored.manifest["skill"]["generator_version"] == "development-skill-renderer-v1"
    assert (
        restored.manifest["skill"]["source_sha256"]
        == sha256(restored.skill_source.encode("utf-8")).hexdigest()
    )
    assert restored.system_prompt.endswith(
        restored.skill_source
        + "\n\n"
        + (
            "主体角色、合同类型、金额、企业立场、有效规范及最低风险政策尚未通过已批准真源确认；"
            "所有抽取值都是待人工核验候选，不得形成确定性判断。"
        )
    )
    assert '"fact_type"' in restored.system_prompt
    assert '"accepted_fact_types":["contract.payment_schedule"]' in restored.system_prompt
    assert (
        restored.manifest["prompt"]["source_sha256"]
        == sha256(restored.system_prompt.encode("utf-8")).hexdigest()
    )
    assert restored.tool_schema["function"]["name"] == "locate_clause"
    assert restored.user_prompt == (
            "先用 locate_clause 检索价款、含税、未税、税额、币种、付款、支付、预付款、结算、验收等候选，"
            "再只按其直接支持的原文抽取当前规则及依赖规则的事实槽位。"
        "所有其他槽位标为 human_review 或 undetermined 且 value=null。"
        "每个 filled 槽位都要单独提供逐字引用。只使用冻结授权材料，不猜测、不扩展材料范围；"
        "不判断规则适用性、企业立场或风险结论。"
    )
    assert "source_sha256" not in restored.user_prompt
    assert (
        restored.manifest["tool_contract"]["source_sha256"]
        == sha256(
            json.dumps(restored.tool_schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_snapshot_freezes_and_validates_every_dependency_rule_source(tmp_path: Path) -> None:
    repository = Path(__file__).parents[1]
    rules_dir = tmp_path / "docs/development/06-rules/rules"
    rules_dir.mkdir(parents=True)
    payment_path = tmp_path / RULE_RELATIVE_PATH
    payment_path.write_bytes((repository / RULE_RELATIVE_PATH).read_bytes())

    dependency = yaml.safe_load((repository / RULE_RELATIVE_PATH).read_text(encoding="utf-8"))
    dependency["rule_id"] = "legal.price.definition"
    dependency["dependencies"]["checks"] = []
    dependency_path = rules_dir / "legal.price.definition.yaml"
    dependency_path.write_text(yaml.safe_dump(dependency, allow_unicode=True), encoding="utf-8")

    snapshot = FrozenDefinitionSnapshot.capture(tmp_path)
    frozen_source = snapshot.dependency_rule_sources["legal.price.definition"]
    assert snapshot.manifest["dependency_graph"]["status"] == "graph_resolved"
    assert snapshot.manifest["dependency_graph"]["execution_order"] == [
        "legal.price.definition",
        "legal.payment_conditions",
    ]
    assert snapshot.system_prompt.count('"slot_id":"payment_schedule"') == 2
    assert snapshot.system_prompt.count('"accepted_fact_types":["contract.payment_schedule"]') == 2

    dependency_path.write_text("changed after queueing", encoding="utf-8")
    restored = FrozenDefinitionSnapshot.from_storage(snapshot.storage_value())
    assert restored.dependency_rule_sources["legal.price.definition"] == frozen_source

    changed = snapshot.storage_value()
    changed["dependency_rule_sources"]["legal.price.definition"] = frozen_source + "\n"
    with pytest.raises(
        DefinitionSnapshotInvalid, match="definition_snapshot_dependency_source_mismatch"
    ):
        FrozenDefinitionSnapshot.from_storage(changed)


def test_definition_snapshot_rejects_source_or_manifest_tampering(tmp_path: Path) -> None:
    target = tmp_path / RULE_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((Path(__file__).parents[1] / RULE_RELATIVE_PATH).read_bytes())
    snapshot = FrozenDefinitionSnapshot.capture(tmp_path)
    changed = deepcopy(snapshot.storage_value())
    changed["skill_source"] += "\nchanged"
    with pytest.raises(DefinitionSnapshotInvalid, match="definition_snapshot_hash_mismatch"):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["manifest"]["dependency_graph"]["definitions"][0]["rule_sha256"] = "0" * 64
    changed["manifest"]["manifest_sha256"] = FrozenDefinitionSnapshot._manifest_digest(
        changed["manifest"]
    )
    with pytest.raises(
        DefinitionSnapshotInvalid, match="definition_snapshot_dependency_graph_invalid"
    ):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    graph = changed["manifest"]["dependency_graph"]
    graph["definitions"][0]["dependencies"] = []
    graph["execution_order"] = ["legal.payment_conditions"]
    graph["blockers"] = []
    graph["status"] = "graph_resolved"
    graph["graph_sha256"] = sha256(
        json.dumps(
            {
                "definitions": graph["definitions"],
                "execution_order": graph["execution_order"],
                "blockers": graph["blockers"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    changed["manifest"]["manifest_sha256"] = FrozenDefinitionSnapshot._manifest_digest(
        changed["manifest"]
    )
    with pytest.raises(
        DefinitionSnapshotInvalid, match="definition_snapshot_dependency_root_mismatch"
    ):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["system_prompt"] += "\nchanged"
    with pytest.raises(DefinitionSnapshotInvalid, match="definition_snapshot_hash_mismatch"):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["user_prompt"] += "\nchanged"
    with pytest.raises(DefinitionSnapshotInvalid, match="definition_snapshot_hash_mismatch"):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["tool_schema"]["function"]["parameters"]["properties"]["query"]["maxLength"] = 1000
    with pytest.raises(DefinitionSnapshotInvalid, match="definition_snapshot_tool_schema_invalid"):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["output_schema"]["properties"]["limitations"]["maxItems"] = 100
    with pytest.raises(DefinitionSnapshotInvalid, match="definition_snapshot_hash_mismatch"):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["manifest"]["skill"]["generator_version"] = "untrusted-renderer"
    changed["manifest"]["manifest_sha256"] = FrozenDefinitionSnapshot._manifest_digest(
        changed["manifest"]
    )
    with pytest.raises(DefinitionSnapshotInvalid, match="definition_snapshot_hash_mismatch"):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["manifest"]["skill"]["generated_at"] = "2026-09-26T09:00:00"
    changed["manifest"]["manifest_sha256"] = FrozenDefinitionSnapshot._manifest_digest(
        changed["manifest"]
    )
    with pytest.raises(
        DefinitionSnapshotInvalid, match="definition_snapshot_skill_metadata_invalid"
    ):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["manifest"]["manifest_sha256"] = "0" * 64
    with pytest.raises(DefinitionSnapshotInvalid, match="definition_snapshot_hash_mismatch"):
        FrozenDefinitionSnapshot.from_storage(changed)

    changed = deepcopy(snapshot.storage_value())
    changed["manifest"]["knowledge_sources"]["legal_normative_refs"] = ["legal.fake-law/v1"]
    changed["manifest"]["manifest_sha256"] = FrozenDefinitionSnapshot._manifest_digest(
        changed["manifest"]
    )
    with pytest.raises(
        DefinitionSnapshotInvalid, match="definition_snapshot_knowledge_refs_mismatch"
    ):
        FrozenDefinitionSnapshot.from_storage(changed)


def test_definition_snapshot_binds_rule_governance_metadata_to_source(tmp_path: Path) -> None:
    repository = Path(__file__).parents[1]
    rule_path = tmp_path / RULE_RELATIVE_PATH
    rule_path.parent.mkdir(parents=True)
    document = yaml.safe_load((repository / RULE_RELATIVE_PATH).read_text(encoding="utf-8"))
    metadata = {
        "knowledge_domain": "legal",
        "basis_kind": "legal_validity_question",
        "applicable_time_basis": "signing_date",
        "review_owner_role": "legal_reviewer",
    }
    document.update(metadata)
    rule_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    captured = FrozenDefinitionSnapshot.capture(tmp_path)
    assert {field: captured.manifest["rule"][field] for field in metadata} == metadata
    assert FrozenDefinitionSnapshot.from_storage(captured.storage_value()) == captured

    changed = captured.storage_value()
    changed["manifest"]["rule"]["knowledge_domain"] = "enterprise"
    changed["manifest"]["manifest_sha256"] = FrozenDefinitionSnapshot._manifest_digest(
        changed["manifest"]
    )
    with pytest.raises(
        DefinitionSnapshotInvalid, match="definition_snapshot_rule_metadata_mismatch"
    ):
        FrozenDefinitionSnapshot.from_storage(changed)


def test_definition_snapshot_freezes_nested_values_and_detaches_storage_copy(
    tmp_path: Path,
) -> None:
    target = tmp_path / RULE_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((Path(__file__).parents[1] / RULE_RELATIVE_PATH).read_bytes())
    profile = {
        "provider": "test",
        "parameters": {"nested": {"values": [1], "tuple_values": ({"item": 1},)}},
    }

    snapshot = FrozenDefinitionSnapshot.capture(tmp_path, model_profile=profile)
    profile["parameters"]["nested"]["values"].append(2)
    profile["parameters"]["nested"]["tuple_values"][0]["item"] = 2
    assert snapshot.model_profile["parameters"]["nested"]["values"] == [1]
    assert snapshot.model_profile["parameters"]["nested"]["tuple_values"] == [{"item": 1}]
    assert deepcopy(snapshot) == snapshot

    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        snapshot.manifest["rule"]["version"] = "9.9.9"
    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        snapshot.tool_schema["function"]["parameters"]["properties"]["query"]["maxLength"] = 1000
    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        snapshot.output_schema["properties"]["limitations"]["maxItems"] = 100
    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        snapshot.model_profile["parameters"]["nested"]["values"].append(3)

    stored = snapshot.storage_value()
    stored["manifest"]["rule"]["version"] = "9.9.9"
    stored["tool_schema"]["function"]["parameters"]["properties"]["query"]["maxLength"] = 1000
    stored["model_profile"]["parameters"]["nested"]["values"].append(4)
    assert snapshot.manifest["rule"]["version"] == "1.0.0"
    assert snapshot.tool_schema["function"]["parameters"]["properties"]["query"]["maxLength"] == 80
    assert snapshot.model_profile["parameters"]["nested"]["values"] == [1]

    restored = FrozenDefinitionSnapshot.from_storage(snapshot.storage_value())
    assert restored == snapshot
    with pytest.raises(TypeError, match="frozen_json_value_is_immutable"):
        restored.manifest["rule"]["version"] = "9.9.9"


def test_worker_rejects_changed_implementation_bundle(tmp_path: Path) -> None:
    definition_root = tmp_path / "definitions"
    implementation_root = tmp_path / "implementation"
    target = definition_root / RULE_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((Path(__file__).parents[1] / RULE_RELATIVE_PATH).read_bytes())

    source = implementation_root / "src/contract_app/api.py"
    source.parent.mkdir(parents=True)
    source.write_text("VERSION = 1\n", encoding="utf-8")
    lock = implementation_root / "uv.lock"
    lock.write_text("version = 1\nrevision = 1\n", encoding="utf-8")
    project = implementation_root / "pyproject.toml"
    project.write_text('[project]\nname = "snapshot-test"\nversion = "1.0.0"\n', encoding="utf-8")

    snapshot = FrozenDefinitionSnapshot.capture(
        definition_root, implementation_root=implementation_root
    )
    bundle = snapshot.manifest["implementation_bundle"]
    assert bundle["source_file_count"] == 1
    assert len(bundle["source_tree_sha256"]) == 64
    assert len(bundle["dependency_lock_sha256"]) == 64

    for target in (source, lock, project):
        original = target.read_bytes()
        target.write_bytes(original + b"\n# changed after run creation\n")
        with pytest.raises(DefinitionSnapshotInvalid, match="definition_snapshot_hash_mismatch"):
            FrozenDefinitionSnapshot.from_storage(
                snapshot.storage_value(), implementation_root=implementation_root
            )
        target.write_bytes(original)


def test_model_profile_is_frozen_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTRACT_REVIEW_MODEL", "review-model-v3")
    monkeypatch.setenv("CONTRACT_REVIEW_MODEL_API_KEY", "never-persist-this")
    monkeypatch.setenv("CONTRACT_REVIEW_MODEL_BASE_URL", "https://gateway.example/v1/tenant-secret")
    profile = capture_model_profile()
    assert profile["model"] == "review-model-v3"
    assert (
        profile["endpoint_sha256"]
        == sha256(b"https://gateway.example/v1/tenant-secret").hexdigest()
    )
    assert "never-persist-this" not in json.dumps(profile)
    assert "tenant-secret" not in json.dumps(profile)
    changed = dict(profile, model="another-model")
    assert model_profile_digest(profile) != model_profile_digest(changed)


def test_configured_model_uses_the_frozen_generation_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("agentscope")
    monkeypatch.setenv("CONTRACT_REVIEW_MODEL", "review-model-v3")
    monkeypatch.setenv("CONTRACT_REVIEW_MODEL_API_KEY", "local-test-secret")
    profile = capture_model_profile()

    from contract_app.workflow.contract_review import _configured_model

    model = _configured_model(profile)
    assert model.model == profile["model"]
    assert model.parameters.model_dump() == profile["parameters"]["generation"]
    assert model.stream is profile["parameters"]["stream"]
    assert model.max_retries == profile["parameters"]["max_retries"]
    assert model.context_size == profile["parameters"]["context_size"]
