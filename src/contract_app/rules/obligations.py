"""Closed, fail-closed contract for 14-category upstream obligation candidates.

This is an untrusted model-output contract, not a legal assessment. Evidence
must still be rebound to the authorized frozen contract pair and verified
against source text and retrieval traces before persistence or presentation.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from contract_app.documents.artifacts import parsed_document_sha256
from contract_app.documents.models import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    ParsedDocument,
    has_material_parser_warnings,
)
from contract_app.workflow.output_contract import BoundedText

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ObligationCategory(StrEnum):
    WORK_SCOPE_DELIVERABLES = "work_scope_deliverables"
    DELIVERY_SCHEDULE = "delivery_schedule"
    ACCEPTANCE_REMEDIATION = "acceptance_remediation"
    QUALITY_SERVICE_ASSURANCE = "quality_service_assurance"
    WARRANTY_DEFECT_HANDLING = "warranty_defect_handling"
    COMPLIANCE_QUALIFICATION_SITE_REQUIREMENTS = "compliance_qualification_site_requirements"
    DATA_CONFIDENTIALITY_SECURITY = "data_confidentiality_security"
    INTELLECTUAL_PROPERTY_THIRD_PARTY_RIGHTS = "intellectual_property_third_party_rights"
    CHANGES_PROJECT_COORDINATION = "changes_project_coordination"
    PAYMENT_COMMERCIAL_TERMS = "payment_commercial_terms"
    LIABILITY_RISK_ALLOCATION = "liability_risk_allocation"
    SUBCONTRACTING_PERSONNEL = "subcontracting_personnel"
    SUSPENSION_TERMINATION_EXIT = "suspension_termination_exit"
    DOCUMENTS_REPORTS_AUDIT = "documents_reports_audit"


OBLIGATION_CATEGORY_DEFINITIONS = (
    (ObligationCategory.WORK_SCOPE_DELIVERABLES, "工作范围与交付物"),
    (ObligationCategory.DELIVERY_SCHEDULE, "交付计划"),
    (ObligationCategory.ACCEPTANCE_REMEDIATION, "验收与整改"),
    (ObligationCategory.QUALITY_SERVICE_ASSURANCE, "质量、服务与保障"),
    (ObligationCategory.WARRANTY_DEFECT_HANDLING, "质保与缺陷处理"),
    (ObligationCategory.COMPLIANCE_QUALIFICATION_SITE_REQUIREMENTS, "合规、资质与现场要求"),
    (ObligationCategory.DATA_CONFIDENTIALITY_SECURITY, "数据、保密与安全"),
    (ObligationCategory.INTELLECTUAL_PROPERTY_THIRD_PARTY_RIGHTS, "知识产权与第三方权利"),
    (ObligationCategory.CHANGES_PROJECT_COORDINATION, "变更与项目配合"),
    (ObligationCategory.PAYMENT_COMMERCIAL_TERMS, "付款与商业条件"),
    (ObligationCategory.LIABILITY_RISK_ALLOCATION, "违约责任与风险分配"),
    (ObligationCategory.SUBCONTRACTING_PERSONNEL, "分包与人员安排"),
    (ObligationCategory.SUSPENSION_TERMINATION_EXIT, "暂停、解除与退出"),
    (ObligationCategory.DOCUMENTS_REPORTS_AUDIT, "文件、报告与审计"),
)
OBLIGATION_CATEGORY_ORDER = tuple(category for category, _title in OBLIGATION_CATEGORY_DEFINITIONS)


class ObligationEvidenceCandidate(BaseModel):
    """A proposed citation; source identity and offsets are server-bound later."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    side: Literal["upstream", "procurement"]
    kind: Literal["document_span", "absence_evidence"]
    document_version_id: UUID
    page: StrictInt | None = Field(default=None, ge=1)
    quote: BoundedText | None = None
    searched_pages: tuple[StrictInt, ...] = Field(default_factory=tuple, max_length=500)
    searched_sections: tuple[BoundedText, ...] = Field(default_factory=tuple, max_length=100)
    query_terms: tuple[BoundedText, ...] = Field(default_factory=tuple, max_length=100)

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> "ObligationEvidenceCandidate":
        if self.kind == "document_span":
            if self.page is None or self.quote is None:
                raise ValueError("obligation_document_span_incomplete")
            if self.searched_pages or self.searched_sections or self.query_terms:
                raise ValueError("document_span_must_not_claim_search_scope")
        else:
            if self.side != "procurement":
                raise ValueError("upstream_absence_is_not_downstream_noncoverage")
            if self.page is not None or self.quote is not None:
                raise ValueError("absence_evidence_must_not_claim_quote")
            if not self.searched_pages or not self.searched_sections or not self.query_terms:
                raise ValueError("obligation_absence_scope_incomplete")
            if len(self.searched_pages) != len(set(self.searched_pages)):
                raise ValueError("obligation_absence_pages_duplicated")
        return self


class ObligationCategoryCandidate(BaseModel):
    """One model-proposed category; never a final contract-risk conclusion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: ObligationCategory
    state: Literal["candidate", "needs_material", "undetermined", "human_review"]
    applicability: Literal["applicable", "not_applicable", "undetermined"] | None
    coverage: Literal["covered", "partial", "missing", "inconsistent", "not_applicable"] | None
    upstream_evidence: tuple[ObligationEvidenceCandidate, ...] = Field(max_length=12)
    procurement_evidence: tuple[ObligationEvidenceCandidate, ...] = Field(max_length=12)
    limitations: tuple[BoundedText, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def validate_candidate_state(self) -> "ObligationCategoryCandidate":
        for evidence in self.upstream_evidence:
            if evidence.side != "upstream" or evidence.kind != "document_span":
                raise ValueError("upstream_evidence_side_or_kind_invalid")
        for evidence in self.procurement_evidence:
            if evidence.side != "procurement":
                raise ValueError("procurement_evidence_side_invalid")

        if self.state != "candidate":
            if self.applicability is not None or self.coverage is not None:
                raise ValueError("unresolved_obligation_must_not_claim_assessment")
            return self

        if self.applicability is None or self.coverage is None:
            raise ValueError("obligation_candidate_assessment_incomplete")
        if self.applicability == "not_applicable":
            if self.coverage != "not_applicable":
                raise ValueError("not_applicable_obligation_coverage_mismatch")
            if not self.limitations:
                raise ValueError("not_applicable_obligation_requires_reason")
            return self
        if self.applicability != "applicable":
            raise ValueError("candidate_applicability_must_be_resolved")
        if self.coverage == "not_applicable":
            raise ValueError("applicable_obligation_cannot_be_not_applicable")
        if not self.upstream_evidence:
            raise ValueError("applicable_obligation_requires_upstream_evidence")
        if self.coverage == "missing":
            if not any(item.kind == "absence_evidence" for item in self.procurement_evidence):
                raise ValueError("missing_coverage_requires_downstream_absence_scope")
        elif not any(item.kind == "document_span" for item in self.procurement_evidence):
            raise ValueError("comparison_requires_procurement_evidence")
        return self


class ObligationCandidateOutput(BaseModel):
    """Strict model output for a dual-contract comparison pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: tuple[ObligationCategoryCandidate, ...] = Field(max_length=14)
    limitations: tuple[BoundedText, ...] = Field(max_length=40)

    @model_validator(mode="after")
    def reject_duplicate_categories(self) -> "ObligationCandidateOutput":
        categories = [item.category for item in self.categories]
        if len(categories) != len(set(categories)):
            raise ValueError("duplicate_obligation_category")
        return self


def complete_obligation_categories(
    output: ObligationCandidateOutput,
) -> tuple[ObligationCategoryCandidate, ...]:
    """Return all 14 categories, marking every omitted model item unresolved."""
    by_category = {item.category: item for item in output.categories}
    return tuple(
        by_category.get(
            category,
            ObligationCategoryCandidate(
                category=category,
                state="undetermined",
                applicability=None,
                coverage=None,
                upstream_evidence=(),
                procurement_evidence=(),
                limitations=("模型未返回此义务类别；不得将其视为已比对或未覆盖。",),
            ),
        )
        for category in OBLIGATION_CATEGORY_ORDER
    )


@dataclass(frozen=True, slots=True)
class ObligationEvidenceSource:
    """One side of an authorized frozen contract pair supplied by the caller.

    ``parsed_sha256`` is the expected artifact hash from the frozen input. This
    value only binds evidence to those identities; the caller remains
    responsible for loading the document through current authorization and
    frozen-input checks. This module is not an authorization gateway.
    """

    document_version_id: UUID
    source_sha256: str
    parsed_sha256: str
    parsed_document: ParsedDocument

    def __post_init__(self) -> None:
        if (
            _SHA256.fullmatch(self.source_sha256) is None
            or self.parsed_document.source_sha256 != self.source_sha256
        ):
            raise ValueError("obligation_source_hash_mismatch")
        if (
            _SHA256.fullmatch(self.parsed_sha256) is None
            or parsed_document_sha256(self.parsed_document) != self.parsed_sha256
        ):
            raise ValueError("obligation_parsed_artifact_hash_mismatch")
        if not self.parsed_document.parser_version.strip():
            raise ValueError("obligation_parser_version_missing")
        if self.parsed_document.media_type == PDF_MEDIA_TYPE:
            if (
                self.parsed_document.index_source_sha256 != self.source_sha256
                or self.parsed_document.renderer_identity is not None
            ):
                raise ValueError("obligation_pdf_parser_identity_invalid")
        elif self.parsed_document.media_type == DOCX_MEDIA_TYPE:
            if (
                self.parsed_document.index_source_sha256 is None
                or _SHA256.fullmatch(self.parsed_document.index_source_sha256) is None
                or not self.parsed_document.renderer_identity
                or not self.parsed_document.renderer_identity.strip()
            ):
                raise ValueError("obligation_docx_renderer_identity_invalid")
        else:
            raise ValueError("obligation_source_media_type_invalid")


@dataclass(frozen=True, slots=True)
class ObligationSearchTrace:
    """Server-recorded, category-scoped absence search; never model-supplied."""

    category: ObligationCategory
    document_version_id: UUID
    searched_pages: tuple[int, ...]
    searched_sections: tuple[str, ...]
    query_terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.category, ObligationCategory) or not isinstance(
            self.document_version_id, UUID
        ):
            raise ValueError("obligation_search_trace_identity_invalid")
        if (
            not isinstance(self.searched_pages, (tuple, list))
            or not self.searched_pages
            or any(
                isinstance(page, bool) or not isinstance(page, int) or page < 1
                for page in self.searched_pages
            )
        ):
            raise ValueError("obligation_search_trace_incomplete")
        if len(self.searched_pages) != len(set(self.searched_pages)):
            raise ValueError("obligation_search_trace_incomplete")
        if (
            not isinstance(self.searched_sections, (tuple, list))
            or not self.searched_sections
            or any(
                not isinstance(section, str) or not section.strip()
                for section in self.searched_sections
            )
            or not isinstance(self.query_terms, (tuple, list))
            or not self.query_terms
            or any(not isinstance(term, str) or not term.strip() for term in self.query_terms)
        ):
            raise ValueError("obligation_search_trace_incomplete")
        object.__setattr__(self, "searched_pages", tuple(self.searched_pages))
        object.__setattr__(self, "searched_sections", tuple(self.searched_sections))
        object.__setattr__(self, "query_terms", tuple(self.query_terms))


@dataclass(frozen=True, slots=True)
class BoundObligationEvidence:
    """Evidence rebound to exact source bytes and parser coordinates."""

    side: Literal["upstream", "procurement"]
    kind: Literal["document_span", "absence_evidence"]
    document_version_id: UUID
    source_sha256: str
    parser_version: str
    parsed_sha256: str
    index_source_sha256: str | None
    renderer_identity: str | None
    page: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    quote: str | None = None
    quote_sha256: str | None = None
    searched_pages: tuple[int, ...] = ()
    searched_sections: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()
    parser_coverage: Literal["complete"] | None = None


@dataclass(frozen=True, slots=True)
class BoundObligationCategory:
    """One category candidate paired only with its server-bound evidence."""

    category: ObligationCategory
    state: Literal["candidate", "needs_material", "undetermined", "human_review"]
    applicability: Literal["applicable", "not_applicable", "undetermined"] | None
    coverage: Literal["covered", "partial", "missing", "inconsistent", "not_applicable"] | None
    evidence: tuple[BoundObligationEvidence, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoundObligationCandidateOutput:
    """Complete ordered 14-category candidate set with bound evidence only."""

    categories: tuple[BoundObligationCategory, ...]
    # Preserved verbatim from the model response; these are not server findings.
    limitations: tuple[str, ...]


def bind_obligation_evidence(
    candidate: ObligationCategoryCandidate,
    *,
    upstream: ObligationEvidenceSource,
    procurement: ObligationEvidenceSource,
    search_traces: tuple[ObligationSearchTrace, ...] = (),
) -> tuple[BoundObligationEvidence, ...]:
    """Bind citations to the caller's fixed, authorized pair.

    This verifies byte identity, side, page and exact unique quotes. Absence
    evidence additionally requires a server search trace for the same
    obligation category and procurement document, covering every page of a
    warning-free parse. The caller must construct source and trace values from
    its authorization/snapshot and actual retrieval service.
    """
    if upstream.document_version_id == procurement.document_version_id:
        raise ValueError("obligation_contract_pair_not_distinct")
    sources = {"upstream": upstream, "procurement": procurement}
    traces_by_category_and_document = {
        (trace.category, trace.document_version_id): trace for trace in search_traces
    }
    if len(traces_by_category_and_document) != len(search_traces):
        raise ValueError("obligation_search_trace_duplicated")

    bound: list[BoundObligationEvidence] = []
    all_evidence = (*candidate.upstream_evidence, *candidate.procurement_evidence)
    for evidence in all_evidence:
        source = sources[evidence.side]
        if evidence.document_version_id != source.document_version_id:
            raise ValueError("obligation_evidence_outside_frozen_pair")
        parsed = source.parsed_document
        if evidence.kind == "document_span":
            if evidence.page is None or evidence.quote is None:
                raise ValueError("obligation_document_span_incomplete")
            if not 1 <= evidence.page <= len(parsed.pages):
                raise ValueError("obligation_evidence_page_invalid")
            page_text = parsed.pages[evidence.page - 1]
            if page_text.count(evidence.quote) != 1:
                raise ValueError("obligation_evidence_quote_not_unique")
            start_offset = page_text.index(evidence.quote)
            bound.append(
                BoundObligationEvidence(
                    side=evidence.side,
                    kind="document_span",
                    document_version_id=source.document_version_id,
                    source_sha256=source.source_sha256,
                    parser_version=parsed.parser_version,
                    parsed_sha256=source.parsed_sha256,
                    index_source_sha256=parsed.index_source_sha256,
                    renderer_identity=parsed.renderer_identity,
                    page=evidence.page,
                    start_offset=start_offset,
                    end_offset=start_offset + len(evidence.quote),
                    quote=evidence.quote,
                    quote_sha256=sha256(evidence.quote.encode("utf-8")).hexdigest(),
                )
            )
            continue

        trace = traces_by_category_and_document.get(
            (candidate.category, source.document_version_id)
        )
        if trace is None:
            raise ValueError("obligation_absence_search_trace_missing")
        expected_pages = tuple(range(1, len(parsed.pages) + 1))
        if (
            not parsed.pages
            or has_material_parser_warnings(parsed.warnings)
            or trace.searched_pages != expected_pages
        ):
            raise ValueError("obligation_absence_source_incomplete")
        if (
            tuple(evidence.searched_pages) != trace.searched_pages
            or tuple(evidence.searched_sections) != trace.searched_sections
            or tuple(evidence.query_terms) != trace.query_terms
        ):
            raise ValueError("obligation_absence_scope_not_executed")
        bound.append(
            BoundObligationEvidence(
                side="procurement",
                kind="absence_evidence",
                document_version_id=source.document_version_id,
                source_sha256=source.source_sha256,
                parser_version=parsed.parser_version,
                parsed_sha256=source.parsed_sha256,
                index_source_sha256=parsed.index_source_sha256,
                renderer_identity=parsed.renderer_identity,
                searched_pages=trace.searched_pages,
                searched_sections=trace.searched_sections,
                query_terms=trace.query_terms,
                parser_coverage="complete",
            )
        )
    return tuple(bound)


def bind_obligation_candidate_output(
    output: ObligationCandidateOutput,
    *,
    upstream: ObligationEvidenceSource,
    procurement: ObligationEvidenceSource,
    search_traces: tuple[ObligationSearchTrace, ...] = (),
) -> BoundObligationCandidateOutput:
    """Complete and bind the entire candidate response as one pure operation.

    Omitted model categories remain undetermined. Every supplied citation is
    rebound to the same fixed contract pair; any invalid citation aborts the
    whole return value rather than yielding a partial bound comparison. This
    performs no authorization, retrieval, or legal applicability decision.
    """
    bound_categories = tuple(
        BoundObligationCategory(
            category=candidate.category,
            state=candidate.state,
            applicability=candidate.applicability,
            coverage=candidate.coverage,
            evidence=bind_obligation_evidence(
                candidate,
                upstream=upstream,
                procurement=procurement,
                search_traces=search_traces,
            ),
            limitations=candidate.limitations,
        )
        for candidate in complete_obligation_categories(output)
    )
    if tuple(item.category for item in bound_categories) != OBLIGATION_CATEGORY_ORDER:
        raise ValueError("obligation_bound_category_order_invalid")
    return BoundObligationCandidateOutput(
        categories=bound_categories,
        limitations=output.limitations,
    )


def obligation_candidate_output_schema() -> dict[str, object]:
    """Return a detached JSON Schema for freezing into a future run definition."""
    return ObligationCandidateOutput.model_json_schema()
