"""Bounded M1 payment-clause candidate extraction over a frozen case snapshot.

The model can locate candidate text, but application code validates every cited
span and the emitted judgment remains undetermined until missing facts and
approved legal/enterprise policy are supplied.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import sleep
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from contract_app.documents.models import ParsedDocument, TextBlock, has_material_parser_warnings
from contract_app.documents.storage import ObjectRef, ObjectStore, create_object_store
from contract_app.retrieval.authorization import (
    AuthorizationProjector,
    AuthorizedPageIndex,
    TaskAccess,
)
from contract_app.retrieval.pageindex_adapter import (
    PageIndexAdapter,
    RetrievalDenied,
    local_pageindex_adapter,
)
from contract_app.rules.calculations import CalculationInputInvalid, check_price_basis
from contract_app.rules.compiler import (
    CompiledRule,
    RulePlan,
    RuleValidationError,
    load_development_rule_bytes,
)
from contract_app.rules.runtime import RuleAssessment, RuleEvidence, RuleRun, SlotSubmission
from contract_app.workflow.cancellation import CancellationSignal
from contract_app.workflow.definitions import (
    DefinitionSnapshotInvalid,
    FrozenDefinitionSnapshot,
)
from contract_app.workflow.dependency_result_reference import (
    DependencyResultReference,
    DependencyResultReferenceInvalid,
    resolve_dependency_result_reference,
)
from contract_app.workflow.execution_budget import resolve_review_execution_budget
from contract_app.workflow.execution_trace import ExecutionTraceEvent, ExecutionTraceRecorder
from contract_app.workflow.job_store import BoundRunDenied, JobStore, LeasedTask
from contract_app.workflow.model_profile import capture_model_profile, model_profile_digest
from contract_app.workflow.output_contract import CandidateOutput
from contract_app.workflow.process_isolation import run_cancellable_process
from contract_app.workflow.rule_plan_execution import RulePlanExecutionSnapshot
from contract_app.workflow.worker import Worker, WorkerTaskContext


class ReviewInputInvalid(ValueError):
    """A durable contract-review task is not bound to its frozen input."""


def _fallbacks_for_unreturned_slots(
    rule: CompiledRule, returned_slot_ids: set[str]
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Preserve every rule slot when the model omits one from its response."""
    missing = tuple(slot for slot in rule.slots if slot.slot_id not in returned_slot_ids)
    candidates: tuple[dict[str, Any], ...] = tuple(
        {
            "slot_id": slot.slot_id,
            "state": "human_review" if slot.validation_rules else "undetermined",
            "value": None,
            "fact_type": None,
            "evidence_ids": [],
        }
        for slot in missing
    )
    limitations = tuple(f"模型未返回槽位 {slot.slot_id} 的候选值。" for slot in missing)
    return candidates, limitations


def _rule_plan_limitations(plan: RulePlan, assessment: RuleAssessment) -> tuple[str, ...]:
    """Explain the frozen rule's current execution boundary without inferring a finding."""
    notes: list[str] = []
    if assessment.status == "undetermined":
        notes.append("规则适用性尚未确定；合同类型、签约角色和司法辖区等事实需要确认。")
    elif assessment.status == "not_applicable":
        notes.append("当前已确认事实不满足该规则的适用范围。")
    elif assessment.status == "blocked":
        notes.append("规则依赖的前置检查尚未完成。")
    if plan.blocked_by_checks:
        notes.append("未完成的前置检查：" + "、".join(plan.blocked_by_checks) + "。")
    if plan.publication_blockers:
        notes.append(
            f"当前开发规则有 {len(plan.publication_blockers)} 项发布阻断；"
            "本次不据此生成正式风险结论。"
        )
    return tuple(notes)


@dataclass(frozen=True, slots=True)
class ReviewSource:
    document_version_id: UUID
    source_sha256: str
    parsed_sha256: str
    parsed_size_bytes: int
    storage_backend: str
    media_type: str
    parser_version: str
    rendered_pdf_sha256: str | None
    renderer_identity: str | None
    warnings: tuple[str, ...]
    relation: str
    pageindex_configuration_sha256: str


@dataclass(frozen=True, slots=True)
class ReviewContext:
    access: TaskAccess
    sources: tuple[ReviewSource, ...]
    definitions: FrozenDefinitionSnapshot | None
    fact_set_snapshot_id: UUID | None
    confirmed_facts: dict[str, Any]
    dependency_result_references: tuple[dict[str, Any], ...]
    research: dict[str, Any] | None = None


class ContractReviewProcessor:
    """Candidate locator that fails closed on stale authorization or bad citations."""

    def __init__(
        self,
        database_url: str,
        tenant_id: UUID,
        object_store: ObjectStore,
        pageindex: PageIndexAdapter,
        model: Any,
        *,
        timeout_seconds: float = 120,
        max_tool_calls: int = 3,
        model_profile: dict[str, Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_tool_calls <= 0:
            raise ValueError("invalid_contract_review_budget")
        self.database_url = database_url
        self.tenant_id = tenant_id
        self.job_store = JobStore(database_url)
        self.object_store = object_store
        self.projector = AuthorizationProjector(database_url)
        self.gateway = AuthorizedPageIndex(self.projector, pageindex)
        self.model = model
        self.model_profile = model_profile
        self.timeout_seconds = timeout_seconds
        self.max_tool_calls = max_tool_calls

    def __call__(
        self, payload: dict[str, Any], task: LeasedTask, worker_context: WorkerTaskContext
    ) -> dict[str, Any]:
        from agentscope.agent import Agent, ReActConfig
        from agentscope.message import TextBlock, UserMsg
        from agentscope.tool import FunctionTool, ToolChunk, Toolkit

        from contract_app.agent.boundary import AgentCallBoundary
        from contract_app.agent.trace_middleware import ExecutionTraceMiddleware

        context = self._load_context(payload, task)
        parsed_documents = self._load_parsed_documents(context)
        definitions = context.definitions
        bound_profile = (context.research.get("model_profile") if context.research is not None
                         else definitions.model_profile if definitions is not None else None)
        if self.model_profile is not None and model_profile_digest(
            self.model_profile
        ) != model_profile_digest(bound_profile or {}):
            raise ReviewInputInvalid("review_model_profile_mismatch")
        if context.research is not None:
            from contract_app.workflow.research_review import run_research

            def persist_research_trace(event: ExecutionTraceEvent) -> None:
                if not self.job_store.record_execution_trace_event(self.tenant_id, task, event):
                    raise ReviewInputInvalid("review_task_lease_lost")

            research_trace = ExecutionTraceRecorder(event_sink=persist_research_trace)
            research_snapshot = context.research
            def check_research_access(channel: str) -> None:
                from contract_app.auth_security import _principal_for_user, set_tenant

                if not set(parsed_documents).issubset(self.projector.indexed_documents(context.access)):
                    raise RetrievalDenied("input_snapshot_sources_not_authorized")
                with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                    set_tenant(connection, self.tenant_id)
                    principal = _principal_for_user(connection, self.tenant_id, context.access.actor_id)
                    selected = [source for source in research_snapshot["sources"]
                        if channel == "all" or source["kind"] == ("company" if channel == "company" else "legal")]
                    if any(source["kind"] == "company" for source in selected) and not principal.permissions.intersection(
                        {"company_rules.use", "company_rules.manage"}
                    ):
                        raise RetrievalDenied("knowledge_source_not_authorized")
                    for source in selected:
                        if not connection.execute("SELECT 1 FROM knowledge_documents WHERE tenant_id=%s "
                            "AND id=%s AND content_sha256=%s AND status='published'",
                            (self.tenant_id, UUID(source["id"]), source["content_sha256"])).fetchone():
                            raise RetrievalDenied("knowledge_source_changed")
            if any(has_material_parser_warnings(source.warnings) for source in context.sources):
                return self._result("ai.contract.dual_review", "1", (),
                    ("合同存在解析覆盖告警，请补齐材料后进行双维审核。",), "needs_material",
                    None, research_trace.snapshot(), summary="合同解析存在缺口，尚未执行双维审核。")
            reviewed = run_research(model=self.model, snapshot=context.research,
                documents=parsed_documents, sources=context.sources, access=context.access,
                gateway=self.gateway, trace=research_trace, cancellation=worker_context.cancelled,
                confirmed_facts=context.confirmed_facts, check_access=check_research_access)
            return {"results": [reviewed]}
        if definitions is None:
            raise ReviewInputInvalid("run_definition_snapshot_invalid")
        rule = load_development_rule_bytes(definitions.rule_source.encode("utf-8"))
        dependency_rules = {
            dependency_id: load_development_rule_bytes(source.encode("utf-8"))
            for dependency_id, source in definitions.dependency_rule_sources.items()
        }
        candidate_rules = (rule, *dependency_rules.values())
        candidate_slots = {
            slot.slot_id: (candidate_rule, slot)
            for candidate_rule in candidate_rules
            for slot in candidate_rule.slots
        }
        dependency_rule_runs: dict[str, tuple[CompiledRule, RulePlan, RuleRun]] = {}
        for dependency_id, dependency_rule in dependency_rules.items():
            dependency_plan = dependency_rule.plan(context.confirmed_facts, frozenset())
            dependency_rule_runs[dependency_id] = (
                dependency_rule,
                dependency_plan,
                RuleRun(
                    dependency_rule,
                    dependency_plan,
                    parsed_documents,
                    facts=context.confirmed_facts,
                ),
            )
        plan = rule.plan(context.confirmed_facts, frozenset())
        graph_definitions = definitions.manifest["dependency_graph"]["definitions"]
        dependency_definitions = {
            item["check_id"]: item
            for item in graph_definitions
            if item["check_id"] in plan.blocked_by_checks
        }
        reference_by_check: dict[str, DependencyResultReference] = {}
        for raw_reference in context.dependency_result_references:
            reference = DependencyResultReference.model_validate(raw_reference)
            definition = dependency_definitions.get(reference.check_id)
            if (
                definition is None
                or reference.check_id in reference_by_check
                or reference.dependency_rule_id != definition["rule_id"]
                or reference.dependency_rule_version != definition["rule_version"]
                or reference.dependency_rule_sha256 != definition["rule_sha256"]
            ):
                raise ReviewInputInvalid("dependency_result_reference_rule_binding_invalid")
            reference_by_check[reference.check_id] = reference
        if context.dependency_result_references and set(reference_by_check) != set(
            plan.blocked_by_checks
        ):
            raise ReviewInputInvalid("dependency_result_reference_set_incomplete")
        validator = RuleRun(rule, plan, parsed_documents, facts=context.confirmed_facts)
        assessment = validator.assess()
        if context.access.input_snapshot_id is None:
            raise ReviewInputInvalid("task_snapshot_binding_invalid")
        rule_plan_execution = RulePlanExecutionSnapshot.capture(
            input_snapshot_id=context.access.input_snapshot_id,
            fact_set_snapshot_id=context.fact_set_snapshot_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            rule_source_sha256=rule.source_sha256,
            plan_sha256=plan.plan_sha256,
            facts_sha256=plan.facts_sha256,
            assessment_status=assessment.status,
            blocked_by_checks=plan.blocked_by_checks,
            missing_slots=assessment.missing_slots,
            unresolved_minimum_judgment_indexes=(assessment.unresolved_minimum_judgment_indexes),
        )
        rule_plan_limitations = _rule_plan_limitations(plan, assessment)
        execution_budget = resolve_review_execution_budget(
            rule, worker_timeout_ceiling=self.timeout_seconds
        )

        # Record fingerprints of the exact development-only definitions used by
        # this execution. Task results are append-only, so the manifest remains
        # available even if these working files are later edited. This is an
        # identity record, not a published/approved ruleset.
        definition_manifest = definitions.manifest

        def persist_trace_event(event: ExecutionTraceEvent) -> None:
            if not self.job_store.record_execution_trace_event(self.tenant_id, task, event):
                raise ReviewInputInvalid("review_task_lease_lost")

        execution_trace = ExecutionTraceRecorder(event_sink=persist_trace_event)

        if any(has_material_parser_warnings(source.warnings) for source in context.sources):
            return self._result(
                rule.rule_id,
                rule.version,
                (),
                (
                    *rule_plan_limitations,
                    "存在解析告警；需先确认解析覆盖，不能据此形成付款条款判断。",
                ),
                "needs_material",
                definition_manifest,
                execution_trace.snapshot(),
                fact_set_snapshot_id=context.fact_set_snapshot_id,
                rule_plan_execution=rule_plan_execution,
                dependency_result_references=context.dependency_result_references,
            )

        exposed_locations: set[tuple[UUID, int]] = set()
        exposed_ranges: dict[tuple[UUID, int], list[tuple[int, int]]] = {}
        tool_calls: list[str] = []
        source_by_id = {source.document_version_id: source for source in context.sources}

        def locate_clause(query: str) -> ToolChunk:
            """Find exact candidates for one or more pipe-separated terms."""
            boundary.before_tool()
            queries = [item.strip() for item in query.split("|")]
            if (
                not query.strip()
                or len(query) > 80
                or len(queries) > 12
                or any(
                    not item
                    or len(item) > 80
                    or any(ord(character) < 32 for character in item)
                    for item in queries
                )
            ):
                raise ValueError("search_query_invalid")
            tool_calls.append(query)
            matches: list[dict[str, Any]] = []
            seen_matches: set[tuple[UUID, int, int]] = set()
            for search_query in queries:
                normalized_query = search_query.casefold()
                for document_id, parsed in parsed_documents.items():
                    for page_number, page_text in enumerate(parsed.pages, start=1):
                        offset = page_text.casefold().find(normalized_query)
                        if offset < 0:
                            continue
                        # PageIndex is the only model-facing retrieval path. It also
                        # rechecks the current grant and frozen snapshot per read.
                        indexed_page = self.gateway.page(context.access, document_id, page_number)
                        if not indexed_page:
                            continue
                        exposed_locations.add((document_id, page_number))
                        excerpt_start = max(0, offset - 220)
                        excerpt_end = min(
                            len(page_text), offset + len(search_query) + 420
                        )
                        match_key = (document_id, page_number, excerpt_start)
                        if match_key not in seen_matches:
                            seen_matches.add(match_key)
                            exposed_ranges.setdefault((document_id, page_number), []).append(
                                (excerpt_start, excerpt_end)
                            )
                            matches.append(
                                {
                                    "document_version_id": str(document_id),
                                    "page": page_number,
                                    "matched_query": search_query,
                                    "excerpt_start": excerpt_start,
                                    "excerpt": page_text[excerpt_start:excerpt_end],
                                }
                            )
                        break
                    if len(matches) >= 18:
                        break
                if len(matches) >= 18:
                    break
            execution_trace.record_payload(
                "tool_exchange",
                {"tool": "locate_clause", "query": query, "matches": matches},
            )
            return ToolChunk(content=[TextBlock(text=json.dumps(matches, ensure_ascii=False))])

        frozen_function_schema = definitions.tool_schema["function"]
        toolkit = Toolkit(
            tools=[
                FunctionTool(
                    locate_clause,
                    name=frozen_function_schema["name"],
                    description=frozen_function_schema["description"],
                    input_schema=frozen_function_schema["parameters"],
                    is_read_only=True,
                )
            ]
        )
        runtime_tool_schemas = asyncio.run(toolkit.get_tool_schemas())
        if runtime_tool_schemas != [definitions.tool_schema]:
            raise ReviewInputInvalid("review_tool_schema_runtime_mismatch")
        agent = Agent(
            name="contract-payment-candidate-locator",
            system_prompt=definitions.system_prompt,
            model=self.model,
            toolkit=toolkit,
            middlewares=[ExecutionTraceMiddleware(execution_trace)],
            react_config=ReActConfig(max_iters=execution_budget.max_rounds),
        )
        boundary = AgentCallBoundary(
            timeout_seconds=execution_budget.timeout_seconds,
            max_tool_calls=self.max_tool_calls,
        )
        response = asyncio.run(
            boundary.run(
                agent,
                UserMsg(
                    name="contract-review-worker",
                    content=definitions.user_prompt,
                ),
                cancellation=worker_context.cancelled,
            )
        )
        text = "\n".join(block.text for block in response.content if isinstance(block, TextBlock))
        if len(text.encode("utf-8")) > 128 * 1024:
            raise ValueError("review_candidate_output_too_large")
        try:
            candidate_output = CandidateOutput.model_validate_json(text)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValueError("review_candidate_json_invalid") from exc
        evidence: list[dict[str, Any]] = []
        limitations = list(candidate_output.limitations)
        evidence_by_key: dict[tuple[UUID, int, str], str] = {}

        def add_document_evidence(document_id: UUID, page_number: int, quote: str) -> str:
            if document_id not in source_by_id:
                raise ValueError("review_candidate_evidence_invalid")
            if (document_id, page_number) not in exposed_locations:
                raise ValueError("review_candidate_location_not_exposed")
            parsed = parsed_documents[document_id]
            if not 1 <= page_number <= len(parsed.pages):
                raise ValueError("review_candidate_page_invalid")
            page_text = parsed.pages[page_number - 1]
            if page_text.count(quote) != 1:
                raise ValueError("review_candidate_quote_not_unique")
            start_offset = page_text.index(quote)
            candidate_key = (document_id, page_number, quote)
            if not any(
                exposed_start <= start_offset and start_offset + len(quote) <= exposed_end
                for exposed_start, exposed_end in exposed_ranges.get((document_id, page_number), ())
            ):
                raise ValueError("review_candidate_quote_not_exposed")
            existing_id = evidence_by_key.get(candidate_key)
            if existing_id is not None:
                return existing_id
            evidence_id = str(uuid4())
            evidence_by_key[candidate_key] = evidence_id
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "kind": "document_span",
                    "support": "context",
                    "source_version": source_by_id[document_id].source_sha256,
                    "document_version_id": str(document_id),
                    "page": page_number,
                    "start_offset": start_offset,
                    "end_offset": start_offset + len(quote),
                    "quote": quote,
                    "text_hash": sha256(quote.encode("utf-8")).hexdigest(),
                }
            )

            return evidence_id

        for observation in candidate_output.observations:
            add_document_evidence(
                observation.document_version_id, observation.page, observation.quote
            )

        slot_candidates: list[dict[str, Any]] = []
        slot_candidate_ids: set[str] = set()
        dependency_slot_candidates: dict[str, list[dict[str, Any]]] = {
            dependency_id: [] for dependency_id in dependency_rules
        }
        dependency_slot_candidate_ids: dict[str, set[str]] = {
            dependency_id: set() for dependency_id in dependency_rules
        }
        trusted_submissions: dict[str, SlotSubmission] = {}
        dependency_trusted_submissions: dict[str, dict[str, SlotSubmission]] = {
            dependency_id: {} for dependency_id in dependency_rules
        }
        for candidate in candidate_output.slot_candidates:
            candidate_definition = candidate_slots.get(candidate.slot_id)
            if candidate_definition is None:
                raise ValueError("review_candidate_slot_not_in_rule")
            candidate_rule, rule_slot = candidate_definition
            target_slot_ids = (
                slot_candidate_ids
                if candidate_rule is rule
                else dependency_slot_candidate_ids[candidate_rule.rule_id]
            )
            target_slot_ids.add(candidate.slot_id)
            citation_list: list[RuleEvidence] = []
            evidence_ids: list[str] = []
            for item in candidate.evidence:
                evidence_ids.append(
                    add_document_evidence(item.document_version_id, item.page, item.quote)
                )
                page_text = parsed_documents[item.document_version_id].pages[item.page - 1]
                start_offset = page_text.index(item.quote)
                citation_list.append(
                    RuleEvidence(
                        kind="document_span",
                        document_version_id=item.document_version_id,
                        source_sha256=source_by_id[item.document_version_id].source_sha256,
                        page=item.page,
                        start_offset=start_offset,
                        end_offset=start_offset + len(item.quote),
                        quote=item.quote,
                        quote_sha256=sha256(item.quote.encode("utf-8")).hexdigest(),
                    )
                )
            citations = tuple(citation_list)
            submission = SlotSubmission(
                candidate.slot_id,
                candidate.state,
                candidate.value,
                citations,
                candidate.fact_type,
            )
            try:
                if candidate_rule is rule:
                    validator.validate_candidate_submission(submission)
                else:
                    dependency_rule_runs[candidate_rule.rule_id][2].validate_candidate_submission(
                        submission
                    )
            except RuleValidationError as exc:
                if not str(exc).startswith("slot_validation_rule_unimplemented:"):
                    raise
                submission = SlotSubmission(
                    candidate.slot_id,
                    "human_review",
                    evidence=citations,
                )
                validator.validate_candidate_submission(submission)
                limitations.append(
                    f"槽位 {candidate.slot_id} 的执行校验尚未接通；已丢弃模型值并转人工复核。"
                )
            normalized_candidate = {
                "slot_id": candidate.slot_id,
                "state": submission.state,
                "value": submission.value,
                "fact_type": submission.fact_type,
                "evidence_ids": evidence_ids,
            }
            if candidate_rule is rule:
                slot_candidates.append(normalized_candidate)
            else:
                dependency_slot_candidates[candidate_rule.rule_id].append(normalized_candidate)
            if candidate_rule is rule:
                trusted_submissions[candidate.slot_id] = submission
            else:
                dependency_trusted_submissions[candidate_rule.rule_id][candidate.slot_id] = (
                    submission
                )
                limitations.append(
                    f"依赖规则 {candidate_rule.rule_id} 的槽位 {candidate.slot_id} 已保存为候选，"
                    "当前运行尚未提交跨规则完成凭据。"
                )

        if plan.applicability == "applicable":
            for rule_slot in rule.slots:
                trusted_submission = trusted_submissions.get(rule_slot.slot_id)
                if trusted_submission is None:
                    continue
                try:
                    validator.submit(trusted_submission)
                except RuleValidationError as exc:
                    if str(exc) not in {"slot_dependency_unresolved", "slot_not_required"}:
                        raise
        for dependency_id, (
            dependency_rule,
            dependency_plan,
            dependency_run,
        ) in dependency_rule_runs.items():
            if dependency_plan.applicability != "applicable":
                continue
            for rule_slot in dependency_rule.slots:
                trusted_submission = dependency_trusted_submissions[dependency_id].get(
                    rule_slot.slot_id
                )
                if trusted_submission is None:
                    continue
                try:
                    dependency_run.submit(trusted_submission)
                except RuleValidationError as exc:
                    if str(exc) not in {"slot_dependency_unresolved", "slot_not_required"}:
                        raise

        fact_candidates: list[dict[str, Any]] = []
        for fact_candidate in candidate_output.fact_candidates:
            evidence_ids = [
                add_document_evidence(item.document_version_id, item.page, item.quote)
                for item in fact_candidate.evidence
            ]
            fact_candidates.append(
                {
                    "predicate": fact_candidate.predicate,
                    "state": fact_candidate.state,
                    "value": fact_candidate.value,
                    "evidence_ids": evidence_ids,
                }
            )

        missing_candidates, missing_limitations = _fallbacks_for_unreturned_slots(
            rule, slot_candidate_ids
        )
        slot_candidates.extend(missing_candidates)
        limitations.extend(missing_limitations)
        for dependency_id, dependency_rule in dependency_rules.items():
            dependency_missing, dependency_limitations = _fallbacks_for_unreturned_slots(
                dependency_rule, dependency_slot_candidate_ids[dependency_id]
            )
            dependency_slot_candidates[dependency_id].extend(dependency_missing)
            limitations.extend(dependency_limitations)
        price_slot_candidates = [
            candidate
            for candidates in dependency_slot_candidates.values()
            for candidate in candidates
        ]
        price_values = {item["slot_id"]: item["value"] for item in price_slot_candidates}
        price_basis = price_values.get("price_basis")
        price_sources = price_values.get("price_sources")
        payment_basis = price_values.get("payment_basis")
        if isinstance(price_basis, dict):
            try:
                tax_check = check_price_basis(
                    tax_inclusive_total=price_basis.get("tax_inclusive_total"),
                    tax_exclusive_total=price_basis.get("tax_exclusive_total"),
                    tax_amount=price_basis.get("tax_amount"),
                    currency=price_basis.get("currency"),
                )
            except (CalculationInputInvalid, TypeError):
                limitations.append("价款候选存在无法安全计算的金额，需人工核对。")
            else:
                if tax_check.tax_consistent is False:
                    limitations.append("含税价、未税价与税额不一致，需人工核对。")
                elif tax_check.tax_consistent is True:
                    limitations.append(
                        "开发态核算提示：含税价减未税价与税额候选一致；仅供人工核对，不构成规则判断。"
                    )
        if isinstance(price_sources, list):
            source_amounts = {
                (source.get("currency"), source.get("amount"))
                for source in price_sources
                if isinstance(source, dict)
            }
            if len(source_amounts) > 1:
                limitations.append("正文、价款表、附件或补充协议的价款来源不一致，需人工核对。")
        if isinstance(price_basis, dict) and isinstance(payment_basis, list):
            confirmed_total = context.confirmed_facts.get("contract.total_amount")
            for stage in payment_basis:
                if not isinstance(stage, dict):
                    continue
                if stage.get("denominator") == "unspecified":
                    limitations.append("付款比例分母未明确，需人工核对。")
                    continue
                if stage.get("denominator") != "contract_total":
                    continue
                denominator = confirmed_total or price_basis.get("tax_inclusive_total")
                if not denominator:
                    limitations.append("合同总价候选缺失，无法核算付款比例金额，需人工核对。")
                    continue
                try:
                    check = check_price_basis(
                        tax_inclusive_total=price_basis.get("tax_inclusive_total"),
                        tax_exclusive_total=price_basis.get("tax_exclusive_total"),
                        tax_amount=price_basis.get("tax_amount"),
                        currency=price_basis.get("currency"),
                        fixed_amount=stage.get("fixed_amount"),
                        percentage=stage.get("percentage"),
                        denominator=denominator,
                    )
                except (CalculationInputInvalid, TypeError):
                    limitations.append("价款候选存在无法安全计算的金额，需人工核对。")
                    continue
                if check.fixed_percentage_consistent is False:
                    limitations.append("固定付款金额与付款比例计算不一致；按开发口径固定金额优先。")
                elif check.fixed_percentage_consistent is True:
                    limitations.append(
                        "开发态核算提示：固定付款金额与按合同总价候选计算的比例金额一致；仅供人工核对。"
                    )
        self.projector.require_result_sources(
            context.access,
            frozenset(UUID(item["document_version_id"]) for item in evidence)
            if evidence
            else frozenset(source_by_id),
        )
        limitations.extend(
            [
                *rule_plan_limitations,
                "付款槽位内容均为模型抽取候选，未执行完整规则适用性或法律/企业判断。",
                "合同角色、合同类型、金额、已批准企业立场、法律依据和最低风险政策未确认；不得自动通过。",
                "必须由有权审核人复核适用性、完整性及候选引用。",
            ]
        )
        final_assessment = validator.assess()
        rule_plan_execution = RulePlanExecutionSnapshot.capture(
            input_snapshot_id=context.access.input_snapshot_id,
            fact_set_snapshot_id=context.fact_set_snapshot_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            rule_source_sha256=rule.source_sha256,
            plan_sha256=plan.plan_sha256,
            facts_sha256=plan.facts_sha256,
            assessment_status=final_assessment.status,
            blocked_by_checks=plan.blocked_by_checks,
            missing_slots=final_assessment.missing_slots,
            unresolved_minimum_judgment_indexes=(
                final_assessment.unresolved_minimum_judgment_indexes
            ),
        )
        if not tool_calls:
            limitations.append("模型未执行来源检索工具；本次未定位到可验证条款。")
        dependency_results: list[dict[str, Any]] = []
        for dependency_id, (
            dependency_rule,
            dependency_plan,
            dependency_run,
        ) in dependency_rule_runs.items():
            dependency_assessment = dependency_run.assess()
            dependency_execution = RulePlanExecutionSnapshot.capture(
                input_snapshot_id=context.access.input_snapshot_id,
                fact_set_snapshot_id=context.fact_set_snapshot_id,
                rule_id=dependency_rule.rule_id,
                rule_version=dependency_rule.version,
                rule_source_sha256=dependency_rule.source_sha256,
                plan_sha256=dependency_plan.plan_sha256,
                facts_sha256=dependency_plan.facts_sha256,
                assessment_status=dependency_assessment.status,
                blocked_by_checks=dependency_plan.blocked_by_checks,
                missing_slots=dependency_assessment.missing_slots,
                unresolved_minimum_judgment_indexes=(
                    dependency_assessment.unresolved_minimum_judgment_indexes
                ),
            )
            dependency_candidates = dependency_slot_candidates[dependency_id]
            dependency_evidence_ids = {
                evidence_id
                for candidate in dependency_candidates
                for evidence_id in candidate["evidence_ids"]
            }
            dependency_evidence = tuple(
                item for item in evidence if item["evidence_id"] in dependency_evidence_ids
            )
            dependency_results.append(
                self._result(
                    dependency_rule.rule_id,
                    dependency_rule.version,
                    dependency_evidence,
                    tuple(limitations),
                    "undetermined",
                    definition_manifest,
                    execution_trace.snapshot(),
                    slot_candidates=tuple(dependency_candidates),
                    fact_set_snapshot_id=context.fact_set_snapshot_id,
                    rule_plan_execution=dependency_execution,
                    summary=(
                        "价款规则已在本次冻结输入上执行开发态候选校验；"
                        "结论尚未形成，不构成通过或不通过。"
                    ),
                )["results"][0]
            )
        return self._result(
            rule.rule_id,
            rule.version,
            tuple(evidence),
            tuple(limitations),
            "undetermined",
            definition_manifest,
            execution_trace.snapshot(),
            tuple(slot_candidates),
            tuple(fact_candidates),
            context.fact_set_snapshot_id,
            rule_plan_execution,
            additional_results=tuple(dependency_results),
            dependency_result_references=context.dependency_result_references,
        )

    def _load_context(self, payload: dict[str, Any], task: LeasedTask) -> ReviewContext:
        snapshot_value = payload.get("input_snapshot_id")
        try:
            expected_snapshot_id = UUID(str(snapshot_value))
        except (TypeError, ValueError) as exc:
            raise ReviewInputInvalid("input_snapshot_id_invalid") from exc
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)", (str(self.tenant_id),)
            )
            run = connection.execute(
                """
                SELECT run.case_id, run.started_by, run.input_snapshot_id, run.status,
                       task.payload,
                       snapshot.purpose, snapshot.require_amount
                FROM tasks AS task
                JOIN audit_runs AS run
                  ON run.tenant_id = task.tenant_id AND run.id = task.run_id
                JOIN input_snapshots AS snapshot
                  ON snapshot.tenant_id = run.tenant_id
                 AND snapshot.case_id = run.case_id AND snapshot.id = run.input_snapshot_id
                WHERE task.tenant_id = %s AND task.id = %s AND task.run_id = %s
                  AND task.task_type = 'contract_review' AND task.status = 'running'
                  AND run.status = 'running'
                """,
                (self.tenant_id, task.id, task.run_id),
            ).fetchone()
            if run is None or run["input_snapshot_id"] != expected_snapshot_id:
                raise ReviewInputInvalid("task_snapshot_binding_invalid")
            if run["payload"] != payload:
                raise ReviewInputInvalid("task_payload_binding_invalid")
            fact_set_snapshot_id: UUID | None = None
            confirmed_facts: dict[str, Any] = {}
            bound_fact_set = payload.get("fact_set_snapshot_id")
            if bound_fact_set is not None:
                try:
                    fact_set_snapshot_id = UUID(str(bound_fact_set))
                except (TypeError, ValueError) as exc:
                    raise ReviewInputInvalid("fact_set_snapshot_id_invalid") from exc
                fact_set = connection.execute(
                    "SELECT input_snapshot_id, facts, content_hash FROM fact_set_snapshots "
                    "WHERE tenant_id = %s AND case_id = %s AND id = %s",
                    (self.tenant_id, run["case_id"], fact_set_snapshot_id),
                ).fetchone()
                if fact_set is None or fact_set["input_snapshot_id"] != expected_snapshot_id:
                    raise ReviewInputInvalid("fact_set_snapshot_binding_invalid")
                facts = fact_set["facts"]
                expected_hash = sha256(
                    json.dumps(
                        facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                if expected_hash != fact_set["content_hash"] or not isinstance(facts, dict):
                    raise ReviewInputInvalid("fact_set_snapshot_integrity_invalid")
                for predicate, entry in facts.items():
                    if (
                        predicate
                        not in {
                            "contract.contract_type",
                            "contract.party_role",
                            "contract.jurisdiction",
                            "contract.total_amount",
                        }
                        or not isinstance(entry, dict)
                        or entry.get("verification_status") != "verified"
                        or not isinstance(entry.get("value"), str)
                    ):
                        raise ReviewInputInvalid("fact_set_snapshot_contract_invalid")
                    confirmed_facts[predicate] = entry["value"]
            definition_row = connection.execute(
                "SELECT snapshot FROM run_definition_snapshots "
                "WHERE tenant_id = %s AND run_id = %s",
                (self.tenant_id, task.run_id),
            ).fetchone()
            if definition_row is None:
                raise ReviewInputInvalid("run_definition_snapshot_missing")
            try:
                if definition_row["snapshot"].get("research") is not None:
                    from contract_app.workflow.research_review import validate_research
                    validate_research(definition_row["snapshot"]["research"])
                    definitions = None
                else:
                    definitions = FrozenDefinitionSnapshot.from_storage(definition_row["snapshot"])
            except (DefinitionSnapshotInvalid, TypeError, ValueError) as exc:
                raise ReviewInputInvalid("run_definition_snapshot_invalid") from exc
            access = TaskAccess(
                self.tenant_id,
                run["case_id"],
                run["started_by"],
                run["purpose"],
                run["require_amount"],
                run["input_snapshot_id"],
            )
            rows = connection.execute(
                """
                SELECT frozen.document_version_id, frozen.source_sha256,
                       frozen.rendered_pdf_sha256, frozen.renderer_identity,
                       frozen.relation, frozen.pageindex_configuration_sha256,
                       frozen.parsed_sha256 AS frozen_parsed_sha256,
                       frozen.parser_version AS frozen_parser_version,
                       document.parsed_sha256,
                       document.parsed_size_bytes, document.storage_backend,
                       document.media_type, document.parser_version, document.warnings
                FROM input_snapshot_documents AS frozen
                JOIN document_versions AS document
                  ON document.tenant_id = frozen.tenant_id
                 AND document.id = frozen.document_version_id
                 AND document.source_sha256 = frozen.source_sha256
                 AND document.parsed_sha256 = frozen.parsed_sha256
                 AND document.parser_version = frozen.parser_version
                 AND document.rendered_pdf_sha256 IS NOT DISTINCT FROM frozen.rendered_pdf_sha256
                 AND document.renderer_identity IS NOT DISTINCT FROM frozen.renderer_identity
                 AND frozen.parsed_sha256 IS NOT NULL
                 AND frozen.parser_version IS NOT NULL
                JOIN document_indexes AS indexed
                  ON indexed.tenant_id = frozen.tenant_id
                 AND indexed.document_version_id = frozen.document_version_id
                 AND indexed.source_sha256 = frozen.source_sha256
                 AND indexed.pageindex_configuration_sha256 =
                     frozen.pageindex_configuration_sha256
                 AND indexed.rendered_pdf_sha256 IS NOT DISTINCT FROM frozen.rendered_pdf_sha256
                 AND indexed.renderer_identity IS NOT DISTINCT FROM frozen.renderer_identity
                WHERE frozen.tenant_id = %s AND frozen.snapshot_id = %s
                ORDER BY frozen.relation, frozen.document_version_id
                """,
                (self.tenant_id, expected_snapshot_id),
            ).fetchall()
            expected_count = connection.execute(
                "SELECT count(*) AS total FROM input_snapshot_documents "
                "WHERE tenant_id = %s AND snapshot_id = %s",
                (self.tenant_id, expected_snapshot_id),
            ).fetchone()
            assert expected_count is not None
            if len(rows) != expected_count["total"]:
                raise ReviewInputInvalid("frozen_parse_or_index_identity_changed")
            allowed = self.projector.indexed_documents_in_transaction(access, connection)
            frozen_ids = {item["document_version_id"] for item in rows}
            if not frozen_ids or not frozen_ids.issubset(allowed):
                raise RetrievalDenied("input_snapshot_sources_not_authorized")
            if not any(item["relation"] == "main" for item in rows):
                raise ReviewInputInvalid("main_contract_missing")
            sources = tuple(
                ReviewSource(
                    document_version_id=item["document_version_id"],
                    source_sha256=item["source_sha256"],
                    parsed_sha256=item["frozen_parsed_sha256"],
                    parsed_size_bytes=item["parsed_size_bytes"],
                    storage_backend=item["storage_backend"],
                    media_type=item["media_type"],
                    parser_version=item["frozen_parser_version"],
                    rendered_pdf_sha256=item["rendered_pdf_sha256"],
                    renderer_identity=item["renderer_identity"],
                    warnings=tuple(item["warnings"] or ()),
                    relation=item["relation"],
                    pageindex_configuration_sha256=item["pageindex_configuration_sha256"],
                )
                for item in rows
            )
        raw_references = payload.get("dependency_result_references", [])
        if not isinstance(raw_references, list) or len(raw_references) > 64:
            raise ReviewInputInvalid("dependency_result_references_invalid")
        try:
            references = tuple(
                DependencyResultReference.model_validate(item).model_dump(mode="json")
                for item in raw_references
            )
        except ValidationError as exc:
            raise ReviewInputInvalid("dependency_result_reference_invalid") from exc
        if any(
            reference["case_id"] != str(run["case_id"])
            or reference["input_snapshot_id"] != str(expected_snapshot_id)
            or reference["fact_set_snapshot_id"]
            != (str(fact_set_snapshot_id) if fact_set_snapshot_id is not None else None)
            or reference["is_completion_proof"] is not False
            for reference in references
        ):
            raise ReviewInputInvalid("dependency_result_reference_binding_invalid")
        source_views = {}
        try:
            for reference in references:
                source_run_id = UUID(reference["source_run_id"])
                if source_run_id not in source_views:
                    source_views[source_run_id] = self.job_store.get_run_results(
                        self.tenant_id, run["started_by"], source_run_id
                    )
                verified_reference = resolve_dependency_result_reference(
                    source_views[source_run_id],
                    expected_case_id=run["case_id"],
                    expected_input_snapshot_id=expected_snapshot_id,
                    expected_fact_set_snapshot_id=fact_set_snapshot_id,
                    check_id=reference["check_id"],
                    dependency_rule_id=reference["dependency_rule_id"],
                    dependency_rule_version=reference["dependency_rule_version"],
                    dependency_rule_sha256=reference["dependency_rule_sha256"],
                )
                if verified_reference.model_dump(mode="json") != reference:
                    raise ReviewInputInvalid("dependency_result_reference_content_changed")
        except (BoundRunDenied, DependencyResultReferenceInvalid, ValueError) as exc:
            raise ReviewInputInvalid("dependency_result_reference_source_invalid") from exc
        return ReviewContext(
            access,
            sources,
            definitions,
            fact_set_snapshot_id,
            confirmed_facts,
            references,
            definition_row["snapshot"].get("research"),
        )

    def _load_parsed_documents(self, context: ReviewContext) -> dict[UUID, ParsedDocument]:
        parsed: dict[UUID, ParsedDocument] = {}
        for source in context.sources:
            if source.storage_backend != self.object_store.backend:
                raise ReviewInputInvalid("document_storage_backend_mismatch")
            reference = ObjectRef(
                self.tenant_id,
                context.access.case_id,
                source.parsed_sha256,
                source.parsed_size_bytes,
            )
            data = self.object_store.get(reference)
            try:
                raw = json.loads(data)
                blocks = tuple(
                    TextBlock(
                        page=block["page"],
                        text=block["text"],
                        start_offset=block["start_offset"],
                        end_offset=block["end_offset"],
                        bbox=tuple(block["bbox"]) if block["bbox"] is not None else None,
                        kind=block["kind"],
                        confidence=block["confidence"],
                    )
                    for block in raw["blocks"]
                )
                document = ParsedDocument(
                    source_sha256=raw["source_sha256"],
                    media_type=raw["media_type"],
                    parser_version=raw["parser_version"],
                    pages=tuple(raw["pages"]),
                    blocks=blocks,
                    warnings=tuple(raw["warnings"]),
                    index_source_sha256=raw["index_source_sha256"],
                    renderer_identity=raw["renderer_identity"],
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ReviewInputInvalid("parsed_artifact_invalid") from exc
            if (
                document.source_sha256 != source.source_sha256
                or document.media_type != source.media_type
                or document.parser_version != source.parser_version
                or document.index_source_sha256
                != (source.rendered_pdf_sha256 or source.source_sha256)
                or document.renderer_identity != source.renderer_identity
            ):
                raise ReviewInputInvalid("parsed_artifact_binding_invalid")
            parsed[source.document_version_id] = document
        return parsed

    @staticmethod
    def _result(
        rule_id: str,
        rule_version: str,
        evidence: tuple[dict[str, Any], ...],
        limitations: tuple[str, ...],
        judgment: str,
        definition_manifest: dict[str, Any] | None,
        execution_trace: dict[str, Any] | None = None,
        slot_candidates: tuple[dict[str, Any], ...] = (),
        fact_candidates: tuple[dict[str, Any], ...] = (),
        fact_set_snapshot_id: UUID | None = None,
        rule_plan_execution: RulePlanExecutionSnapshot | None = None,
        additional_results: tuple[dict[str, Any], ...] = (),
        dependency_result_references: tuple[dict[str, Any], ...] = (),
        summary: str | None = None,
    ) -> dict[str, Any]:
        resolved_summary = summary or (
            "付款条款候选已定位；付款风险判断尚未执行，不构成风险结论。"
            if evidence
            else "本轮未形成可验证的付款条款引用；不能据此认定合同缺少付款约定。"
        )
        primary_result = {
            "result_id": str(uuid4()),
            "rule_id": rule_id,
            "rule_version": rule_version,
            "definition_manifest": definition_manifest,
            "rule_plan_execution": (
                rule_plan_execution.model_dump(mode="json")
                if rule_plan_execution is not None
                else None
            ),
            "execution_trace": execution_trace,
            "fact_set_snapshot_id": (
                str(fact_set_snapshot_id) if fact_set_snapshot_id is not None else None
            ),
            "judgment": judgment,
            "severity": "undetermined",
            "summary": resolved_summary,
            "evidence": list(evidence),
            "slot_candidates": list(slot_candidates),
            "fact_candidates": list(fact_candidates),
            "limitations": list(limitations),
            "consumed_dependency_result_references": list(dependency_result_references),
            "created_at": datetime.now(UTC).isoformat(),
        }
        return {"results": [primary_result, *additional_results]}


class IsolatedContractReviewHandler:
    """Rebuild the review processor in a killable child process per task."""

    def __init__(
        self,
        tenant_id: UUID,
        pageindex_model: str,
        model_profile: dict[str, Any],
        *,
        timeout_seconds: float = 120,
        process_timeout_seconds: float = 180,
        max_tool_calls: int = 3,
    ) -> None:
        if (
            not pageindex_model
            or timeout_seconds <= 0
            or process_timeout_seconds <= timeout_seconds
            or max_tool_calls <= 0
        ):
            raise ValueError("invalid_isolated_review_configuration")
        self.tenant_id = tenant_id
        self.pageindex_model = pageindex_model
        self.model_profile = model_profile
        self.timeout_seconds = timeout_seconds
        self.process_timeout_seconds = process_timeout_seconds
        self.max_tool_calls = max_tool_calls

    def __call__(
        self, payload: dict[str, Any], task: LeasedTask, worker_context: WorkerTaskContext
    ) -> dict[str, Any]:
        return run_cancellable_process(
            _run_contract_review_in_child,
            (
                self.tenant_id,
                self.pageindex_model,
                self.model_profile,
                self.timeout_seconds,
                self.max_tool_calls,
                payload,
                task,
            ),
            cancellation=worker_context.cancelled,
            timeout_seconds=self.process_timeout_seconds,
        )


def _run_contract_review_in_child(
    tenant_id: UUID,
    pageindex_model: str,
    model_profile: dict[str, Any],
    timeout_seconds: float,
    max_tool_calls: int,
    payload: dict[str, Any],
    task: LeasedTask,
    *,
    cancellation: CancellationSignal,
) -> dict[str, Any]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("review_database_unavailable")
    pageindex = local_pageindex_adapter(
        Path(os.environ.get("PAGEINDEX_STORAGE_PATH", "./var/pageindex")), pageindex_model
    )
    processor = ContractReviewProcessor(
        database_url,
        tenant_id,
        create_object_store(),
        pageindex,
        _configured_model(model_profile),
        timeout_seconds=timeout_seconds,
        max_tool_calls=max_tool_calls,
        model_profile=model_profile,
    )
    return processor(payload, task, WorkerTaskContext(cancellation))


def _configured_model(profile: dict[str, Any] | None = None) -> Any:
    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel
    from pydantic import SecretStr

    api_key = os.environ.get("CONTRACT_REVIEW_MODEL_API_KEY")
    profile = profile or capture_model_profile()
    model_name = profile["model"]
    parameters = profile["parameters"]
    if not api_key:
        raise SystemExit("CONTRACT_REVIEW_MODEL_API_KEY and CONTRACT_REVIEW_MODEL are required")
    # The profile binds endpoint identity via a digest; the raw URL stays in
    # process configuration and is never copied into the durable run snapshot.
    base_url = os.environ.get("CONTRACT_REVIEW_MODEL_BASE_URL")
    credential = OpenAICredential(api_key=SecretStr(api_key), base_url=base_url)
    return OpenAIChatModel(
        credential=credential,
        model=model_name,
        parameters=OpenAIChatModel.Parameters(**parameters["generation"]),
        stream=parameters["stream"],
        max_retries=parameters["max_retries"],
        retry_delay=parameters["retry_delay"],
        context_size=parameters["context_size"],
    )


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    tenant_value = os.environ.get("CONTRACT_REVIEW_TENANT_ID")
    pageindex_model = os.environ.get("PAGEINDEX_MODEL")
    if not database_url or not tenant_value or not pageindex_model:
        raise SystemExit("DATABASE_URL, CONTRACT_REVIEW_TENANT_ID and PAGEINDEX_MODEL are required")
    tenant_id = UUID(tenant_value)
    model_profile = capture_model_profile()
    worker_id = os.environ.get(
        "CONTRACT_REVIEW_WORKER_ID",
        f"contract-review-{socket.gethostname()}-{os.getpid()}",
    )
    processor = IsolatedContractReviewHandler(
        tenant_id,
        pageindex_model,
        model_profile=model_profile,
        timeout_seconds=240,
        process_timeout_seconds=300,
    )
    from contract_app.workflow.upstream_comparison import IsolatedUpstreamComparisonHandler

    comparison_processor = IsolatedUpstreamComparisonHandler(
        tenant_id,
        pageindex_model,
        model_profile=model_profile,
    )
    worker = Worker(
        JobStore(database_url),
        tenant_id,
        worker_id,
        {},
        contextual_handlers={
            "contract_review": processor,
            "upstream_comparison": comparison_processor,
        },
        lease_seconds=180,
        heartbeat_interval_seconds=1.0,
        max_attempts=3,
        retry_delay_seconds=10,
    )
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        if worker.run_once() == "idle":
            sleep(2)


if __name__ == "__main__":
    main()
