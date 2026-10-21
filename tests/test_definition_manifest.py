import json
from hashlib import sha256

import pytest
from pydantic import ValidationError

from contract_app.api_runs import ExecutionDefinitionManifest


def test_execution_definition_manifest_requires_matching_canonical_hash() -> None:
    dependency_graph_body = {
        "definitions": [
            {
                "check_id": "legal.payment_conditions",
                "rule_id": "legal.payment_conditions",
                "rule_version": "1.0.0",
                "rule_sha256": "a" * 64,
                "dependencies": ["legal.price.definition"],
            }
        ],
        "execution_order": [],
        "blockers": ["dependent_check_definition_missing:legal.price.definition"],
    }
    dependency_graph_sha256 = sha256(
        json.dumps(dependency_graph_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    content = {
        "kind": "development_execution_definitions",
        "rule": {
            "rule_id": "legal.payment_conditions",
            "version": "1.0.0",
            "knowledge_domain": "legal",
            "basis_kind": "legal_validity_question",
            "applicable_time_basis": "signing_date",
            "review_owner_role": "legal_reviewer",
            "source_path": "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml",
            "source_sha256": "a" * 64,
        },
        "skill": {
            "source_path": (
                "generated:development-skill-renderer-v1/contract-payment-candidate.skill.md"
            ),
            "source_sha256": "b" * 64,
            "generator_version": "development-skill-renderer-v1",
            "status": "development_candidate",
            "rule_sha256": "a" * 64,
            "tool_contract_sha256": "1" * 64,
            "output_schema_sha256": "2" * 64,
            "publication_blockers": ["publication_approval_missing"],
            "generated_at": "2026-09-26T09:00:00+00:00",
        },
        "user_prompt": {
            "source_path": "generated:contract-payment-candidate-locator.user-prompt.v1",
            "source_sha256": "9" * 64,
        },
        "output_contract": {
            "source_path": "generated:contract-payment-candidate-locator.output-schema.v1",
            "source_sha256": "c" * 64,
        },
        "implementation_bundle": {
            "source_tree_sha256": "d" * 64,
            "source_file_count": 10,
            "dependency_lock_sha256": "e" * 64,
            "project_config_sha256": "f" * 64,
            "python_version": "3.13.7",
        },
        "knowledge_sources": {
            "legal_normative_refs": [],
            "enterprise_position_refs": ["enterprise.payment-policy/mvp-v1"],
            "content_binding": "unresolved_development_references",
        },
        "dependency_graph": {
            "kind": "development_rule_dependency_graph",
            "status": "blocked",
            **dependency_graph_body,
            "graph_sha256": dependency_graph_sha256,
            "is_execution_proof": False,
            "is_approval_record": False,
        },
    }
    digest = sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest = ExecutionDefinitionManifest.model_validate({**content, "manifest_sha256": digest})
    assert manifest.manifest_sha256 == digest
    assert manifest.rule.knowledge_domain == "legal"
    assert manifest.rule.review_owner_role == "legal_reviewer"

    with pytest.raises(ValidationError, match="definition_manifest_hash_mismatch"):
        ExecutionDefinitionManifest.model_validate({**content, "manifest_sha256": "c" * 64})

    legacy_content = {**content, "rule": dict(content["rule"])}
    for field in (
        "knowledge_domain",
        "basis_kind",
        "applicable_time_basis",
        "review_owner_role",
    ):
        legacy_content["rule"].pop(field)
    legacy_digest = sha256(
        json.dumps(legacy_content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    legacy_manifest = ExecutionDefinitionManifest.model_validate(
        {**legacy_content, "manifest_sha256": legacy_digest}
    )
    assert legacy_manifest.rule.knowledge_domain is None
    assert legacy_manifest.rule.review_owner_role is None
    assert "knowledge_domain" not in legacy_manifest.model_dump(mode="json")["rule"]
