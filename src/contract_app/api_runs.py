"""HTTP commands and current-member reads for frozen-input audit runs."""

import json
import os
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from contract_app.api_cases import get_database_url
from contract_app.api_errors import COMMON_PROBLEM_RESPONSES, problem_responses
from contract_app.api_snapshots import Principal, require_principal
from contract_app.auth_security import require_permission
from contract_app.retrieval.pageindex_adapter import RetrievalDenied
from contract_app.rules.dependency_graph import (
    CheckRegistryInvalid,
    validate_check_execution_graph_manifest,
)
from contract_app.workflow.dependency_result_reference import (
    DependencyResultReference,
    DependencyResultReferenceInvalid,
    resolve_dependency_result_reference,
)
from contract_app.workflow.execution_trace import ExecutionTraceSnapshot, RunModelUsageSummary
from contract_app.workflow.job_store import (
    BoundRunConflict,
    BoundRunDenied,
    FactSetSnapshotView,
    JobStore,
    RunResultsView,
    RunView,
)
from contract_app.workflow.model_profile import ModelProfileInvalid, capture_model_profile
from contract_app.workflow.rule_plan_execution import RulePlanExecutionSnapshot
from contract_app.workflow.upstream_comparison import comparison_definition

router = APIRouter(
    prefix="/api/v1",
    tags=["Runs"],
    responses=COMMON_PROBLEM_RESPONSES,
    dependencies=[Depends(require_permission("cases.use"))],
)
MAX_RUN_RESULTS_RESPONSE_BYTES = 4 * 1024 * 1024


def get_job_store() -> JobStore:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return JobStore(database_url)


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_snapshot_id: UUID
    dependency_source_run_id: UUID | None = None


class FactConfirmationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: Literal[
        "contract.contract_type",
        "contract.party_role",
        "contract.jurisdiction",
        "contract.total_amount",
    ]
    value: str = Field(min_length=1, max_length=500)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=3)

    @field_validator("value")
    @classmethod
    def value_must_contain_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fact_value_must_not_be_blank")
        return value.strip()


class ConfirmRunFactsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmations: list[FactConfirmationItem] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_request(self) -> "ConfirmRunFactsRequest":
        if not self.reason.strip():
            raise ValueError("reason_must_not_be_blank")
        predicates = [item.predicate for item in self.confirmations]
        if len(predicates) != len(set(predicates)):
            raise ValueError("duplicate_fact_confirmation")
        if any(
            len(item.evidence_ids) != len(set(item.evidence_ids)) for item in self.confirmations
        ):
            raise ValueError("duplicate_fact_evidence")
        return self


class FactSetSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_set_snapshot_id: UUID
    case_id: UUID
    input_snapshot_id: UUID
    source_run_id: UUID
    version: int
    facts: dict[str, Any]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_by: UUID
    reason: str
    created_at: datetime


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    case_id: UUID
    input_snapshot_id: UUID | None
    status: Literal[
        "queued", "running", "waiting", "completed", "failed", "cancelled", "superseded"
    ]
    version: int
    created_at: datetime
    task_type: Literal["contract_review", "upstream_comparison"] | None = None


class UpstreamComparisonEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["upstream", "procurement"]
    kind: Literal["document_span", "absence_evidence"]
    document_version_id: UUID
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_version: str = Field(min_length=1)
    parsed_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_source_sha256: str | None
    renderer_identity: str | None
    page: int | None
    start_offset: int | None
    end_offset: int | None
    quote: str | None
    quote_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    searched_pages: list[int]
    searched_sections: list[str]
    query_terms: list[str]
    parser_coverage: Literal["complete"] | None


class UpstreamComparisonCategoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    state: Literal["candidate", "needs_material", "undetermined", "human_review"]
    applicability: Literal["applicable", "not_applicable", "undetermined"] | None
    coverage: Literal["covered", "partial", "missing", "inconsistent", "not_applicable"] | None
    evidence: list[UpstreamComparisonEvidenceResponse]
    limitations: list[str]


class UpstreamComparisonPairResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upstream_document_version_id: UUID
    upstream_file_name: str
    procurement_document_version_id: UUID
    procurement_file_name: str
    categories: list[UpstreamComparisonCategoryResponse]
    limitations: list[str]


class UpstreamComparisonResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_version: str
    input_snapshot_id: UUID
    candidate_only: Literal[True]
    definition: dict[str, str]
    execution_trace: ExecutionTraceSnapshot
    pairs: list[UpstreamComparisonPairResponse]
    limitations: list[str]


class UpstreamComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunResponse
    result: UpstreamComparisonResultResponse | None


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID
    kind: Literal[
        "document_span",
        "source_record",
        "calculation",
        "normative_reference",
        "human_confirmation",
        "absence_evidence",
    ]
    support: Literal["direct", "corroborating", "context", "contradicting"]
    source_version: str
    document_version_id: UUID | None = None
    page: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    quote: str | None = None
    text_hash: str | None = None
    source_record_ref: str | None = None


class DefinitionReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DevelopmentSkillReference(DefinitionReference):
    generator_version: str = Field(min_length=1, max_length=64)
    status: Literal["development_candidate"]
    rule_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    publication_blockers: list[str]
    generated_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00$")


class SlotCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str
    state: Literal["filled", "needs_material", "conflicted", "undetermined", "human_review"]
    value: Any
    fact_type: str | None = None
    evidence_ids: list[UUID]


class RuleDefinitionReference(DefinitionReference):
    rule_id: str
    version: str
    knowledge_domain: str | None = Field(default=None, exclude_if=lambda value: value is None)
    basis_kind: str | None = Field(default=None, exclude_if=lambda value: value is None)
    applicable_time_basis: str | None = Field(default=None, exclude_if=lambda value: value is None)
    review_owner_role: str | None = Field(default=None, exclude_if=lambda value: value is None)


class ImplementationBundleReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_file_count: int = Field(ge=1)
    dependency_lock_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    project_config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    python_version: str = Field(min_length=1, max_length=32)


class KnowledgeSourceReferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_normative_refs: list[str]
    enterprise_position_refs: list[str]
    content_binding: Literal["unresolved_development_references"]


class DependencyGraphRuleReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    rule_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dependencies: list[str]


class DevelopmentDependencyGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["development_rule_dependency_graph"]
    status: Literal["blocked", "graph_resolved"]
    definitions: list[DependencyGraphRuleReference]
    execution_order: list[str]
    blockers: list[str]
    graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    is_execution_proof: Literal[False]
    is_approval_record: Literal[False]

    @model_validator(mode="after")
    def verify_graph_digest(self) -> "DevelopmentDependencyGraph":
        try:
            validate_check_execution_graph_manifest(self.model_dump(mode="json"))
        except CheckRegistryInvalid as exc:
            raise ValueError("dependency_graph_manifest_invalid") from exc
        return self


class ExecutionDefinitionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["development_execution_definitions"]
    rule: RuleDefinitionReference
    skill: DevelopmentSkillReference
    prompt: DefinitionReference | None = None
    user_prompt: DefinitionReference
    tool_contract: DefinitionReference | None = None
    output_contract: DefinitionReference
    model: DefinitionReference | None = None
    implementation_bundle: ImplementationBundleReference
    knowledge_sources: KnowledgeSourceReferences
    dependency_graph: DevelopmentDependencyGraph
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_manifest_digest(self) -> "ExecutionDefinitionManifest":
        content = self.model_dump(mode="json", exclude={"manifest_sha256"}, exclude_none=True)
        expected = sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if self.manifest_sha256 != expected:
            raise ValueError("definition_manifest_hash_mismatch")
        return self


class FactCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: Literal[
        "contract.contract_type",
        "contract.party_role",
        "contract.jurisdiction",
        "contract.total_amount",
    ]
    state: Literal["candidate", "undetermined"]
    value: str | None
    evidence_ids: list[UUID]

    @model_validator(mode="after")
    def validate_fact_candidate(self) -> "FactCandidateResponse":
        if (self.state == "candidate" and (self.value is None or not self.evidence_ids)) or (
            self.state == "undetermined" and (self.value is not None or self.evidence_ids)
        ):
            raise ValueError("fact_candidate_state_invalid")
        return self

    @model_validator(mode="after")
    def validate_candidate_state(self) -> "FactCandidateResponse":
        if (self.state == "candidate" and (self.value is None or not self.evidence_ids)) or (
            self.state == "undetermined" and (self.value is not None or self.evidence_ids)
        ):
            raise ValueError("fact_candidate_state_invalid")
        return self


class CheckResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: UUID
    task_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rule_id: str
    rule_version: str
    judgment: Literal[
        "passed", "issue_found", "needs_material", "undetermined", "not_applicable", "human_review"
    ]
    severity: Literal["critical", "high", "medium", "low", "info", "undetermined"]
    summary: str | None = None
    research_review: dict[str, Any] | None = None
    evidence: list[EvidenceResponse]
    slot_candidates: list[SlotCandidateResponse] = Field(default_factory=list)
    fact_candidates: list[FactCandidateResponse] = Field(default_factory=list)
    limitations: list[str]
    created_at: datetime
    fact_set_snapshot_id: UUID | None = None
    definition_manifest: ExecutionDefinitionManifest | None = None
    execution_trace: ExecutionTraceSnapshot | None = None
    rule_plan_execution: RulePlanExecutionSnapshot | None = None
    dependency_result_references: list[DependencyResultReference] = Field(default_factory=list)
    consumed_dependency_result_references: list[DependencyResultReference] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_slot_candidate_evidence_links(self) -> "CheckResultResponse":
        evidence_ids = {item.evidence_id for item in self.evidence}
        if any(
            not set(candidate.evidence_ids).issubset(evidence_ids)
            or (candidate.state == "filled" and not candidate.evidence_ids)
            or (candidate.state != "filled" and candidate.value is not None)
            for candidate in self.slot_candidates
        ) or any(
            not set(candidate.evidence_ids).issubset(evidence_ids)
            for candidate in self.fact_candidates
        ):
            raise ValueError("slot_candidate_evidence_binding_invalid")
        return self


class RunResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunResponse
    results: list[CheckResultResponse]
    reported_model_usage: RunModelUsageSummary | None = None
    can_confirm_facts: bool = False

    @model_validator(mode="after")
    def validate_rule_plan_execution_bindings(self) -> "RunResultsResponse":
        for result in self.results:
            snapshot = result.rule_plan_execution
            if result.consumed_dependency_result_references and (
                snapshot is None
                or self.run.input_snapshot_id is None
                or any(
                    reference.case_id != self.run.case_id
                    or reference.input_snapshot_id != self.run.input_snapshot_id
                    or reference.fact_set_snapshot_id != result.fact_set_snapshot_id
                    or reference.source_run_id == self.run.run_id
                    or reference.check_id not in snapshot.blocked_by_checks
                    or reference.is_completion_proof is not False
                    for reference in result.consumed_dependency_result_references
                )
            ):
                raise ValueError("consumed_dependency_result_reference_binding_invalid")
            if snapshot is None:
                continue
            manifest = result.definition_manifest
            manifest_binds_rule = manifest is not None and (
                (
                    manifest.rule.rule_id == snapshot.rule_id
                    and manifest.rule.version == snapshot.rule_version
                    and manifest.rule.source_sha256 == snapshot.rule_source_sha256
                )
                or sum(
                    definition.rule_id == snapshot.rule_id
                    and definition.rule_version == snapshot.rule_version
                    and definition.rule_sha256 == snapshot.rule_source_sha256
                    for definition in manifest.dependency_graph.definitions
                )
                == 1
            )
            if (
                self.run.input_snapshot_id is None
                or snapshot.input_snapshot_id != self.run.input_snapshot_id
                or snapshot.fact_set_snapshot_id != result.fact_set_snapshot_id
                or snapshot.rule_id != result.rule_id
                or snapshot.rule_version != result.rule_version
                or not manifest_binds_rule
            ):
                raise ValueError("rule_plan_execution_binding_invalid")
        return self


def _enforce_run_results_response_limit(response: RunResultsResponse) -> RunResultsResponse:
    if len(response.model_dump_json().encode("utf-8")) > MAX_RUN_RESULTS_RESPONSE_BYTES:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return response


def _dependency_references_by_result_id(
    view: RunResultsView,
) -> dict[str, list[dict[str, Any]]]:
    if view.run.status != "completed" or view.run.input_snapshot_id is None:
        return {}
    references: dict[str, list[dict[str, Any]]] = {}
    for consumer in view.results:
        assessment = consumer.get("rule_plan_execution")
        manifest = consumer.get("definition_manifest")
        if not isinstance(assessment, dict) or not isinstance(manifest, dict):
            continue
        try:
            assessment_snapshot = RulePlanExecutionSnapshot.model_validate(assessment)
            graph = validate_check_execution_graph_manifest(manifest.get("dependency_graph"))
        except (CheckRegistryInvalid, ValueError):
            continue
        graph_definitions = graph.get("definitions")
        if not isinstance(graph_definitions, list):
            continue
        try:
            fact_set_snapshot_id = (
                UUID(str(consumer["fact_set_snapshot_id"]))
                if consumer.get("fact_set_snapshot_id") is not None
                else None
            )
            consumer_result_id = str(UUID(str(consumer["result_id"])))
        except (KeyError, TypeError, ValueError):
            continue
        result_references: list[dict[str, Any]] = []
        for check_id in assessment_snapshot.blocked_by_checks:
            dependency_definitions = [
                item
                for item in graph_definitions
                if isinstance(item, dict) and item.get("check_id") == check_id
            ]
            if len(dependency_definitions) != 1:
                continue
            dependency = dependency_definitions[0]
            try:
                reference = resolve_dependency_result_reference(
                    view,
                    expected_case_id=view.run.case_id,
                    expected_input_snapshot_id=view.run.input_snapshot_id,
                    expected_fact_set_snapshot_id=fact_set_snapshot_id,
                    check_id=check_id,
                    dependency_rule_id=str(dependency["rule_id"]),
                    dependency_rule_version=str(dependency["rule_version"]),
                    dependency_rule_sha256=str(dependency["rule_sha256"]),
                )
            except (DependencyResultReferenceInvalid, KeyError, TypeError, ValueError):
                continue
            result_references.append(reference.model_dump(mode="json"))
        if result_references:
            references[consumer_result_id] = result_references
    return references


def _references_for_new_run(
    source: RunResultsView,
    *,
    case_id: UUID,
    input_snapshot_id: UUID,
    definition_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    if source.run.case_id != case_id or source.run.input_snapshot_id != input_snapshot_id:
        raise DependencyResultReferenceInvalid("dependency_result_run_binding_invalid")
    graph = validate_check_execution_graph_manifest(definition_manifest.get("dependency_graph"))
    graph_definitions = graph.get("definitions")
    if not isinstance(graph_definitions, list):
        raise DependencyResultReferenceInvalid("dependency_result_manifest_invalid")
    root = definition_manifest.get("rule")
    if not isinstance(root, dict):
        raise DependencyResultReferenceInvalid("dependency_result_manifest_invalid")
    root_definition = next(
        (
            item
            for item in graph_definitions
            if isinstance(item, dict) and item.get("rule_id") == root.get("rule_id")
        ),
        None,
    )
    if not isinstance(root_definition, dict):
        raise DependencyResultReferenceInvalid("dependency_result_manifest_invalid")
    dependencies = [
        item
        for item in graph_definitions
        if isinstance(item, dict) and item.get("check_id") in root_definition["dependencies"]
    ]
    if not dependencies:
        return []
    fact_set_ids = {
        UUID(str(result["fact_set_snapshot_id"]))
        if result.get("fact_set_snapshot_id") is not None
        else None
        for result in source.results
    }
    if len(fact_set_ids) != 1:
        raise DependencyResultReferenceInvalid("dependency_result_fact_set_ambiguous")
    expected_fact_set_id = next(iter(fact_set_ids))
    references = []
    for dependency in dependencies:
        reference = resolve_dependency_result_reference(
            source,
            expected_case_id=case_id,
            expected_input_snapshot_id=input_snapshot_id,
            expected_fact_set_snapshot_id=expected_fact_set_id,
            check_id=str(dependency["check_id"]),
            dependency_rule_id=str(dependency["rule_id"]),
            dependency_rule_version=str(dependency["rule_version"]),
            dependency_rule_sha256=str(dependency["rule_sha256"]),
        )
        references.append(reference.model_dump(mode="json"))
    return references


class RunListResponse(BaseModel):
    items: list[RunResponse]
    next_cursor: str | None = None


def _response(run: RunView) -> RunResponse:
    return RunResponse(
        run_id=run.id,
        case_id=run.case_id,
        input_snapshot_id=run.input_snapshot_id,
        status=cast(
            Literal[
                "queued",
                "running",
                "waiting",
                "completed",
                "failed",
                "cancelled",
                "superseded",
            ],
            run.status,
        ),
        version=run.version,
        created_at=run.created_at,
        task_type=cast(
            Literal["contract_review", "upstream_comparison"] | None, run.task_type
        ),
    )


@router.post(
    "/cases/{case_id}/upstream-comparisons",
    operation_id="startUpstreamComparison",
    response_model=RunResponse,
    status_code=202,
    responses=problem_responses(404, 409, 503),
)
def start_upstream_comparison(
    case_id: UUID,
    request: StartRunRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
        ),
    ],
) -> RunResponse:
    if (
        os.environ.get("APP_ENV", "").lower() not in {"development", "test"}
        or os.environ.get("CONTRACT_REVIEW_EXECUTION_ENABLED", "").lower() != "true"
    ):
        raise HTTPException(status_code=503, detail="comparison_execution_disabled")
    try:
        definition = comparison_definition(capture_model_profile())
        run_id = jobs.start_upstream_comparison(
            principal.tenant_id,
            case_id,
            principal.actor_id,
            request.input_snapshot_id,
            idempotency_key,
            definition,
        )
    except BoundRunDenied as exc:
        raise HTTPException(status_code=404, detail="upstream_comparison_snapshot_unavailable") from exc
    except BoundRunConflict as exc:
        raise HTTPException(status_code=409, detail="upstream_comparison_conflict") from exc
    except (ModelProfileInvalid, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="comparison_definition_unavailable") from exc
    return _response(jobs.get_run(principal.tenant_id, principal.actor_id, run_id))


@router.get(
    "/runs/{run_id}/upstream-comparison-results",
    operation_id="getUpstreamComparisonResults",
    description=(
        "Returns the current authorized frozen-pair candidate result. The serialized response "
        "is limited to 4 MiB and oversized responses fail closed."
    ),
    response_model=UpstreamComparisonResponse,
    responses=problem_responses(404, 503),
)
def get_upstream_comparison_results(
    run_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
) -> UpstreamComparisonResponse:
    try:
        view = jobs.get_upstream_comparison_results(
            principal.tenant_id, principal.actor_id, run_id
        )
        result = (
            UpstreamComparisonResultResponse.model_validate(view.result)
            if view.result is not None
            else None
        )
        response = UpstreamComparisonResponse(run=_response(view.run), result=result)
        if len(response.model_dump_json().encode("utf-8")) > MAX_RUN_RESULTS_RESPONSE_BYTES:
            raise HTTPException(status_code=503, detail="service_unavailable")
        return response
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="upstream_comparison_not_available") from exc
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="service_unavailable") from exc


@router.post(
    "/cases/{case_id}/runs",
    operation_id="startAuditRun",
    response_model=RunResponse,
    status_code=202,
    responses=problem_responses(404, 409, 503),
)
def start_run(
    case_id: UUID,
    request: StartRunRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    database_url: Annotated[str, Depends(get_database_url)],
) -> RunResponse:
    if (
        os.environ.get("APP_ENV", "").lower() not in {"development", "test"}
        or os.environ.get("CONTRACT_REVIEW_EXECUTION_ENABLED", "").lower() != "true"
    ):
        raise HTTPException(status_code=503, detail="review_execution_disabled")
    try:
        model_profile = capture_model_profile()
    except (ModelProfileInvalid, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="review_definition_unavailable") from exc
    if request.dependency_source_run_id is not None:
        try:
            source_view = jobs.get_run_results(
                principal.tenant_id, principal.actor_id, request.dependency_source_run_id
            )
            if source_view.run.case_id != case_id or source_view.run.input_snapshot_id != request.input_snapshot_id:
                raise ValueError("dependency_source_scope_mismatch")
        except (
            BoundRunDenied,
            DependencyResultReferenceInvalid,
            CheckRegistryInvalid,
            ValueError,
        ):
            raise HTTPException(status_code=409, detail="dependency_source_unavailable") from None
    from contract_app.retrieval.knowledge import capture_sources
    from contract_app.workflow.research_review import capture_research

    try:
        source_snapshot = capture_sources(database_url, principal.tenant_id)
        if not principal.permissions.intersection({"company_rules.use", "company_rules.manage"}):
            from contract_app.retrieval.knowledge import digest
            source_snapshot["sources"] = [source for source in source_snapshot["sources"]
                                          if source["kind"] != "company"]
            source_snapshot["sources_sha256"] = digest(source_snapshot["sources"])
        storage = {"research": capture_research({**source_snapshot, "model_profile": model_profile})}
    except ValueError as exc:
        raise HTTPException(503, "knowledge_snapshot_unavailable") from exc
    run_id = jobs.start_bound_run(
        principal.tenant_id,
        case_id,
        principal.actor_id,
        request.input_snapshot_id,
        idempotency_key,
        storage,
    )
    return _response(jobs.get_run(principal.tenant_id, principal.actor_id, run_id))


@router.get(
    "/cases/{case_id}/runs",
    operation_id="listCaseRuns",
    response_model=RunListResponse,
    responses=problem_responses(404),
)
def list_case_runs(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RunListResponse:
    try:
        runs, next_cursor = jobs.list_case_runs(
            principal.tenant_id,
            principal.actor_id,
            case_id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_cursor_or_limit") from exc
    return RunListResponse(items=[_response(run) for run in runs], next_cursor=next_cursor)


@router.get(
    "/runs/{run_id}",
    operation_id="getAuditRun",
    response_model=RunResponse,
    responses=problem_responses(404),
)
def get_run(
    run_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
) -> RunResponse:
    return _response(jobs.get_run(principal.tenant_id, principal.actor_id, run_id))


@router.get(
    "/runs/{run_id}/results",
    operation_id="listRunResults",
    description=(
        "Returns the current authorized result projection. The serialized response is limited "
        "to 4 MiB; oversized results fail closed with 503 and are never truncated."
    ),
    response_model=RunResultsResponse,
    responses=problem_responses(404),
)
def list_run_results(
    run_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
) -> RunResultsResponse:
    try:
        view: RunResultsView = jobs.get_run_results(
            principal.tenant_id, principal.actor_id, run_id
        )
    except RetrievalDenied as exc:
        if str(exc) == "result_source_not_authorized":
            raise HTTPException(status_code=403, detail="run_source_access_changed") from None
        raise
    dependency_references = _dependency_references_by_result_id(view)
    if not principal.permissions.intersection({"company_rules.use", "company_rules.manage"}) and any(
        source.get("kind") == "company" for result in view.results
        for source in (result.get("research_review") or {}).get("sources", [])
    ):
        raise HTTPException(403, "knowledge_source_access_changed")
    try:
        results = [
            CheckResultResponse.model_validate(
                {
                    **result,
                    "dependency_result_references": dependency_references.get(
                        str(result.get("result_id")), []
                    ),
                }
            )
            for result in view.results
        ]
    except ValidationError:
        raise HTTPException(status_code=503, detail="service_unavailable") from None
    response = RunResultsResponse(
        run=_response(view.run),
        results=results,
        reported_model_usage=view.reported_model_usage,
        can_confirm_facts=view.can_confirm_facts,
    )
    return _enforce_run_results_response_limit(response)


@router.post(
    "/runs/{run_id}/fact-set-snapshots",
    operation_id="confirmRunFacts",
    response_model=FactSetSnapshotResponse,
    status_code=201,
    responses=problem_responses(404, 409),
)
def confirm_run_facts(
    run_id: UUID,
    body: ConfirmRunFactsRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
        ),
    ],
) -> FactSetSnapshotResponse:
    try:
        snapshot: FactSetSnapshotView = jobs.confirm_run_facts(
            principal.tenant_id,
            principal.actor_id,
            run_id,
            [item.model_dump(mode="json") for item in body.confirmations],
            body.reason,
            idempotency_key,
        )
    except BoundRunDenied as exc:
        raise HTTPException(status_code=404, detail="run_not_available") from exc
    except BoundRunConflict as exc:
        raise HTTPException(status_code=409, detail="fact_confirmation_conflict") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_fact_confirmation") from exc
    return FactSetSnapshotResponse(
        fact_set_snapshot_id=snapshot.id,
        case_id=snapshot.case_id,
        input_snapshot_id=snapshot.input_snapshot_id,
        source_run_id=snapshot.source_run_id,
        version=snapshot.version,
        facts=snapshot.facts,
        content_hash=snapshot.content_hash,
        created_by=snapshot.created_by,
        reason=snapshot.reason,
        created_at=snapshot.created_at,
    )
