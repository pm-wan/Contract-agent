"""Bind a dependency check to one authorized, persisted rule result without completing it."""

import json
import re
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contract_app.rules.dependency_graph import (
    CheckRegistryInvalid,
    validate_check_execution_graph_manifest,
)
from contract_app.workflow.job_store import RunResultsView
from contract_app.workflow.rule_plan_execution import RulePlanExecutionSnapshot

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class DependencyResultReferenceInvalid(ValueError):
    """A persisted result cannot be bound to this dependency request."""


class DependencyResultReference(BaseModel):
    """Content-addressed reference to a RulePlan assessment, never a completion proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["dependency_result_reference"]
    check_id: str = Field(min_length=1, max_length=160)
    dependency_rule_id: str = Field(min_length=1, max_length=160)
    dependency_rule_version: str = Field(min_length=1, max_length=64)
    dependency_rule_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    definition_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_id: UUID
    input_snapshot_id: UUID
    fact_set_snapshot_id: UUID | None
    source_run_id: UUID
    result_id: UUID
    task_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    assessment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_judgment: Literal[
        "passed", "issue_found", "needs_material", "undetermined", "not_applicable", "human_review"
    ]
    assessment_status: Literal[
        "not_applicable", "blocked", "undetermined", "incomplete", "needs_material", "human_review"
    ]
    is_completion_proof: Literal[False]
    reference_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_reference_hash(self) -> "DependencyResultReference":
        content = self.model_dump(mode="json", exclude={"reference_sha256"})
        expected = sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        if self.reference_sha256 != expected:
            raise ValueError("dependency_result_reference_hash_mismatch")
        return self


def _evidence_manifest_sha256(evidence: Any) -> str:
    if not isinstance(evidence, list):
        raise DependencyResultReferenceInvalid("dependency_result_evidence_invalid")
    manifest: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            raise DependencyResultReferenceInvalid("dependency_result_evidence_invalid")
        manifest.append(
            {
                key: item.get(key)
                for key in (
                    "evidence_id",
                    "kind",
                    "support",
                    "source_version",
                    "document_version_id",
                    "page",
                    "start_offset",
                    "end_offset",
                    "text_hash",
                    "source_record_ref",
                )
            }
        )
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _definition_manifest_sha256(manifest: Any) -> str:
    if not isinstance(manifest, dict):
        raise DependencyResultReferenceInvalid("dependency_result_manifest_invalid")
    claimed = manifest.get("manifest_sha256")
    if not isinstance(claimed, str) or not _SHA256_PATTERN.fullmatch(claimed):
        raise DependencyResultReferenceInvalid("dependency_result_manifest_invalid")

    def remove_none(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: remove_none(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [remove_none(item) for item in value]
        return value

    content_without_digest = {
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }
    content = remove_none(content_without_digest)
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if sha256(canonical.encode("utf-8")).hexdigest() != claimed:
        raise DependencyResultReferenceInvalid("dependency_result_manifest_hash_mismatch")
    return claimed


def _manifest_binds_dependency(
    manifest: Any,
    *,
    check_id: str,
    rule_id: str,
    rule_version: str,
    rule_sha256: str,
) -> bool:
    if not isinstance(manifest, dict):
        return False
    graph = manifest.get("dependency_graph")
    if graph is None:
        root = manifest.get("rule")
        return (
            check_id == rule_id
            and isinstance(root, dict)
            and root.get("rule_id") == rule_id
            and root.get("version") == rule_version
            and root.get("source_sha256") == rule_sha256
        )
    try:
        validated_graph = validate_check_execution_graph_manifest(graph)
    except CheckRegistryInvalid:
        return False
    definitions = validated_graph.get("definitions")
    if not isinstance(definitions, list):
        return False
    matches = [
        definition
        for definition in definitions
        if isinstance(definition, dict)
        if definition.get("check_id") == check_id
        and definition.get("rule_id") == rule_id
        and definition.get("rule_version") == rule_version
        and definition.get("rule_sha256") == rule_sha256
    ]
    return len(matches) == 1


def resolve_dependency_result_reference(
    view: RunResultsView,
    *,
    expected_case_id: UUID,
    expected_input_snapshot_id: UUID,
    expected_fact_set_snapshot_id: UUID | None,
    check_id: str,
    dependency_rule_id: str,
    dependency_rule_version: str,
    dependency_rule_sha256: str,
) -> DependencyResultReference:
    """Select one authorized result with exact case/input/fact/definition identity.

    ``view`` must come from ``JobStore.get_run_results`` so its result/task hash and
    current document authorization have already been verified. This helper only
    creates a result reference; it deliberately never infers dependency completion.
    """
    if (
        view.run.case_id != expected_case_id
        or view.run.input_snapshot_id != expected_input_snapshot_id
        or view.run.status != "completed"
    ):
        raise DependencyResultReferenceInvalid("dependency_result_run_binding_invalid")
    if not _SHA256_PATTERN.fullmatch(dependency_rule_sha256):
        raise DependencyResultReferenceInvalid("dependency_result_rule_identity_invalid")

    matches = [
        result
        for result in view.results
        if result.get("rule_id") == dependency_rule_id
        and result.get("rule_version") == dependency_rule_version
        and _manifest_binds_dependency(
            result.get("definition_manifest"),
            check_id=check_id,
            rule_id=dependency_rule_id,
            rule_version=dependency_rule_version,
            rule_sha256=dependency_rule_sha256,
        )
    ]
    if not matches:
        raise DependencyResultReferenceInvalid("dependency_result_missing")
    if len(matches) != 1:
        raise DependencyResultReferenceInvalid("dependency_result_ambiguous")

    result = matches[0]
    manifest_sha256 = _definition_manifest_sha256(result.get("definition_manifest"))
    try:
        assessment = RulePlanExecutionSnapshot.model_validate(result.get("rule_plan_execution"))
        result_id = UUID(str(result["result_id"]))
        task_result_sha256 = str(result["task_result_sha256"])
        judgment = result["judgment"]
    except (KeyError, TypeError, ValueError) as exc:
        raise DependencyResultReferenceInvalid("dependency_result_snapshot_invalid") from exc
    definition_manifest_sha256 = manifest_sha256
    try:
        result_fact_set_snapshot_id = (
            UUID(str(result["fact_set_snapshot_id"]))
            if result.get("fact_set_snapshot_id") is not None
            else None
        )
    except (TypeError, ValueError) as exc:
        raise DependencyResultReferenceInvalid("dependency_result_snapshot_invalid") from exc
    if (
        assessment.input_snapshot_id != expected_input_snapshot_id
        or assessment.fact_set_snapshot_id != expected_fact_set_snapshot_id
        or assessment.fact_set_snapshot_id != result_fact_set_snapshot_id
        or assessment.rule_id != dependency_rule_id
        or assessment.rule_version != dependency_rule_version
        or assessment.rule_source_sha256 != dependency_rule_sha256
        or not _manifest_binds_dependency(
            result.get("definition_manifest"),
            check_id=check_id,
            rule_id=dependency_rule_id,
            rule_version=dependency_rule_version,
            rule_sha256=dependency_rule_sha256,
        )
        or not isinstance(definition_manifest_sha256, str)
        or not _SHA256_PATTERN.fullmatch(definition_manifest_sha256)
        or not _SHA256_PATTERN.fullmatch(task_result_sha256)
        or judgment
        not in {
            "passed",
            "issue_found",
            "needs_material",
            "undetermined",
            "not_applicable",
            "human_review",
        }
    ):
        raise DependencyResultReferenceInvalid("dependency_result_snapshot_binding_invalid")

    content = {
        "kind": "dependency_result_reference",
        "check_id": check_id,
        "dependency_rule_id": dependency_rule_id,
        "dependency_rule_version": dependency_rule_version,
        "dependency_rule_sha256": dependency_rule_sha256,
        "definition_manifest_sha256": definition_manifest_sha256,
        "case_id": str(expected_case_id),
        "input_snapshot_id": str(expected_input_snapshot_id),
        "fact_set_snapshot_id": (
            str(assessment.fact_set_snapshot_id)
            if assessment.fact_set_snapshot_id is not None
            else None
        ),
        "source_run_id": str(view.run.id),
        "result_id": str(result_id),
        "task_result_sha256": task_result_sha256,
        "assessment_sha256": assessment.assessment_sha256,
        "evidence_manifest_sha256": _evidence_manifest_sha256(result.get("evidence")),
        "observed_judgment": judgment,
        "assessment_status": assessment.assessment_status,
        "is_completion_proof": False,
    }
    reference_sha256 = sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    try:
        return DependencyResultReference(**content, reference_sha256=reference_sha256)
    except (TypeError, ValueError) as exc:
        raise DependencyResultReferenceInvalid("dependency_result_reference_invalid") from exc
