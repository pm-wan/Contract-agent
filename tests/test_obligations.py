from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from contract_app.documents.artifacts import parsed_document_sha256
from contract_app.documents.models import DOCX_MEDIA_TYPE, ParsedDocument
from contract_app.rules.obligations import (
    OBLIGATION_CATEGORY_DEFINITIONS,
    OBLIGATION_CATEGORY_ORDER,
    ObligationCandidateOutput,
    ObligationCategory,
    ObligationCategoryCandidate,
    ObligationEvidenceCandidate,
    ObligationEvidenceSource,
    ObligationSearchTrace,
    bind_obligation_candidate_output,
    bind_obligation_evidence,
    complete_obligation_categories,
    obligation_candidate_output_schema,
)


def _span(side: str) -> dict[str, object]:
    return {
        "side": side,
        "kind": "document_span",
        "document_version_id": str(uuid4()),
        "page": 1,
        "quote": "双方约定具体交付期限。",
    }


def _category_candidate(
    category: ObligationCategory,
    *,
    coverage: str = "covered",
    procurement_evidence: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if procurement_evidence is None:
        procurement_evidence = [_span("procurement")]
    return {
        "category": category.value,
        "state": "candidate",
        "applicability": "applicable",
        "coverage": coverage,
        "upstream_evidence": [_span("upstream")],
        "procurement_evidence": procurement_evidence,
        "limitations": [],
    }


def _source(
    texts: tuple[str, ...],
    *,
    warnings: tuple[str, ...] = (),
    document_version_id: UUID | None = None,
) -> ObligationEvidenceSource:
    source_hash = sha256("\n".join(texts).encode("utf-8")).hexdigest()
    parsed_document = ParsedDocument(
        source_sha256=source_hash,
        media_type="application/pdf",
        parser_version="test-parser-v1",
        pages=texts,
        blocks=(),
        warnings=warnings,
        index_source_sha256=source_hash,
    )
    return ObligationEvidenceSource(
        document_version_id=document_version_id or uuid4(),
        source_sha256=source_hash,
        parsed_sha256=parsed_document_sha256(parsed_document),
        parsed_document=parsed_document,
    )


def test_closed_taxonomy_contains_exactly_the_confirmed_fourteen_categories() -> None:
    assert len(OBLIGATION_CATEGORY_ORDER) == 14
    assert len(set(OBLIGATION_CATEGORY_ORDER)) == 14
    assert len(OBLIGATION_CATEGORY_DEFINITIONS) == 14
    assert ObligationCategory.PAYMENT_COMMERCIAL_TERMS in OBLIGATION_CATEGORY_ORDER


def test_candidate_requires_both_contract_sides_and_consistent_coverage() -> None:
    candidate = ObligationCategoryCandidate.model_validate(
        _category_candidate(ObligationCategory.DELIVERY_SCHEDULE)
    )
    assert candidate.state == "candidate"
    assert len(candidate.upstream_evidence) == len(candidate.procurement_evidence) == 1

    invalid = _category_candidate(
        ObligationCategory.DELIVERY_SCHEDULE,
        procurement_evidence=[],
    )
    with pytest.raises(ValidationError, match="comparison_requires_procurement_evidence"):
        ObligationCategoryCandidate.model_validate(invalid)


def test_validated_candidate_is_immutable_after_schema_validation() -> None:
    candidate = ObligationCategoryCandidate.model_validate(
        _category_candidate(ObligationCategory.DELIVERY_SCHEDULE)
    )
    with pytest.raises(ValidationError):
        candidate.coverage = "missing"
    with pytest.raises(AttributeError):
        candidate.upstream_evidence.append(candidate.upstream_evidence[0])  # type: ignore[attr-defined]


def test_missing_coverage_requires_downstream_absence_scope_not_just_no_quote() -> None:
    invalid = _category_candidate(
        ObligationCategory.ACCEPTANCE_REMEDIATION,
        coverage="missing",
        procurement_evidence=[],
    )
    with pytest.raises(ValidationError, match="missing_coverage_requires_downstream_absence_scope"):
        ObligationCategoryCandidate.model_validate(invalid)

    absent = {
        "side": "procurement",
        "kind": "absence_evidence",
        "document_version_id": str(uuid4()),
        "searched_pages": [1, 2],
        "searched_sections": ["验收"],
        "query_terms": ["验收标准", "整改"],
    }
    candidate = ObligationCategoryCandidate.model_validate(
        _category_candidate(
            ObligationCategory.ACCEPTANCE_REMEDIATION,
            coverage="missing",
            procurement_evidence=[absent],
        )
    )
    assert candidate.coverage == "missing"


def test_unresolved_states_cannot_claim_coverage_or_applicability() -> None:
    unresolved = {
        "category": ObligationCategory.DATA_CONFIDENTIALITY_SECURITY.value,
        "state": "needs_material",
        "applicability": None,
        "coverage": None,
        "upstream_evidence": [],
        "procurement_evidence": [],
        "limitations": ["上游合同未取得。"],
    }
    assert ObligationCategoryCandidate.model_validate(unresolved).coverage is None
    unresolved["coverage"] = "missing"
    with pytest.raises(ValidationError, match="unresolved_obligation_must_not_claim_assessment"):
        ObligationCategoryCandidate.model_validate(unresolved)


def test_model_cannot_mark_applicable_category_not_applicable() -> None:
    candidate = _category_candidate(ObligationCategory.WORK_SCOPE_DELIVERABLES)
    candidate["coverage"] = "not_applicable"
    with pytest.raises(ValidationError, match="applicable_obligation_cannot_be_not_applicable"):
        ObligationCategoryCandidate.model_validate(candidate)


def test_model_output_has_no_final_pass_or_no_risk_state() -> None:
    candidate = _category_candidate(ObligationCategory.WORK_SCOPE_DELIVERABLES)
    candidate["state"] = "passed"
    with pytest.raises(ValidationError):
        ObligationCategoryCandidate.model_validate(candidate)


def test_absence_scope_is_downstream_only_and_requires_nonempty_fields() -> None:
    upstream_absence = {
        "side": "upstream",
        "kind": "absence_evidence",
        "document_version_id": str(uuid4()),
        "searched_pages": [1],
        "searched_sections": ["全部"],
        "query_terms": ["付款"],
    }
    with pytest.raises(ValidationError, match="upstream_absence_is_not_downstream_noncoverage"):
        ObligationEvidenceCandidate.model_validate(upstream_absence)


def test_binding_recomputes_side_version_offset_and_quote_hash() -> None:
    upstream = _source(("上游第一条：交付周期为十日。",))
    procurement = _source(("采购第一条：供应商应在十日内交付。",))
    candidate = ObligationCategoryCandidate.model_validate(
        {
            "category": ObligationCategory.DELIVERY_SCHEDULE.value,
            "state": "candidate",
            "applicability": "applicable",
            "coverage": "covered",
            "upstream_evidence": [
                {
                    "side": "upstream",
                    "kind": "document_span",
                    "document_version_id": str(upstream.document_version_id),
                    "page": 1,
                    "quote": "交付周期为十日",
                }
            ],
            "procurement_evidence": [
                {
                    "side": "procurement",
                    "kind": "document_span",
                    "document_version_id": str(procurement.document_version_id),
                    "page": 1,
                    "quote": "应在十日内交付",
                }
            ],
            "limitations": [],
        }
    )

    bound = bind_obligation_evidence(candidate, upstream=upstream, procurement=procurement)

    assert [item.side for item in bound] == ["upstream", "procurement"]
    assert bound[0].start_offset == upstream.parsed_document.pages[0].index("交付周期为十日")
    assert bound[0].end_offset == bound[0].start_offset + len("交付周期为十日")
    assert bound[0].source_sha256 == upstream.source_sha256
    assert bound[0].parser_version == upstream.parsed_document.parser_version
    assert bound[0].parsed_sha256 == upstream.parsed_sha256
    assert bound[0].index_source_sha256 == upstream.source_sha256
    assert bound[0].renderer_identity is None
    assert bound[0].quote_sha256 == sha256("交付周期为十日".encode()).hexdigest()


def test_evidence_source_rejects_a_different_frozen_parse_artifact_hash() -> None:
    source = _source(("解析产物应绑定到冻结输入。",))

    with pytest.raises(ValueError, match="obligation_parsed_artifact_hash_mismatch"):
        ObligationEvidenceSource(
            document_version_id=source.document_version_id,
            source_sha256=source.source_sha256,
            parsed_sha256="0" * 64,
            parsed_document=source.parsed_document,
        )


def test_bound_docx_evidence_preserves_frozen_renderer_identity() -> None:
    original_hash = sha256(b"original docx bytes").hexdigest()
    rendered_hash = sha256(b"canonical rendered pdf").hexdigest()
    docx_document = ParsedDocument(
        source_sha256=original_hash,
        media_type=DOCX_MEDIA_TYPE,
        parser_version="test-docx-parser-v1",
        pages=("上游合同约定十日交付。",),
        blocks=(),
        warnings=("docx_pages_use_canonical_pdf_rendering",),
        index_source_sha256=rendered_hash,
        renderer_identity="libreoffice:26.8.0.3:writer_pdf_Export:test-env-r1",
    )
    upstream = ObligationEvidenceSource(
        document_version_id=uuid4(),
        source_sha256=original_hash,
        parsed_sha256=parsed_document_sha256(docx_document),
        parsed_document=docx_document,
    )
    procurement = _source(("采购合同要求十日内交付。",))
    raw_candidate = _category_candidate(ObligationCategory.DELIVERY_SCHEDULE)
    raw_candidate["upstream_evidence"][0].update(
        {
            "document_version_id": str(upstream.document_version_id),
            "quote": "上游合同约定十日交付。",
        }
    )
    raw_candidate["procurement_evidence"][0].update(
        {
            "document_version_id": str(procurement.document_version_id),
            "quote": "采购合同要求十日内交付。",
        }
    )
    candidate = ObligationCategoryCandidate.model_validate(raw_candidate)

    bound = bind_obligation_evidence(candidate, upstream=upstream, procurement=procurement)

    upstream_evidence = bound[0]
    assert upstream_evidence.parser_version == docx_document.parser_version
    assert upstream_evidence.parsed_sha256 == upstream.parsed_sha256
    assert upstream_evidence.index_source_sha256 == rendered_hash
    assert upstream_evidence.renderer_identity == docx_document.renderer_identity


def test_binding_rejects_wrong_pair_and_ambiguous_or_unavailable_quotes() -> None:
    upstream = _source(("相同引文 相同引文",))
    procurement = _source(("供应商需要履约。",))
    raw_candidate = _category_candidate(
        ObligationCategory.WORK_SCOPE_DELIVERABLES,
        procurement_evidence=[
            {
                "side": "procurement",
                "kind": "document_span",
                "document_version_id": str(procurement.document_version_id),
                "page": 1,
                "quote": "供应商需要履约",
            }
        ],
    )
    raw_candidate["upstream_evidence"] = [
        {
            "side": "upstream",
            "kind": "document_span",
            "document_version_id": str(uuid4()),
            "page": 1,
            "quote": "相同引文",
        }
    ]
    candidate = ObligationCategoryCandidate.model_validate(raw_candidate)
    with pytest.raises(ValueError, match="obligation_evidence_outside_frozen_pair"):
        bind_obligation_evidence(candidate, upstream=upstream, procurement=procurement)

    raw_candidate["upstream_evidence"] = [
        {
            "side": "upstream",
            "kind": "document_span",
            "document_version_id": str(upstream.document_version_id),
            "page": 1,
            "quote": "相同引文",
        }
    ]
    candidate = ObligationCategoryCandidate.model_validate(raw_candidate)
    with pytest.raises(ValueError, match="obligation_evidence_quote_not_unique"):
        bind_obligation_evidence(candidate, upstream=upstream, procurement=procurement)


def test_missing_coverage_requires_complete_server_search_trace_and_parse() -> None:
    upstream = _source(("上游要求提供验收记录。",))
    procurement = _source(("采购条款第一页。", "采购条款第二页。"))
    absence = {
        "side": "procurement",
        "kind": "absence_evidence",
        "document_version_id": str(procurement.document_version_id),
        "searched_pages": [1, 2],
        "searched_sections": ["验收章节"],
        "query_terms": ["验收记录"],
    }
    raw_candidate = _category_candidate(
        ObligationCategory.ACCEPTANCE_REMEDIATION,
        coverage="missing",
        procurement_evidence=[absence],
    )
    raw_candidate["upstream_evidence"] = [
        {
            "side": "upstream",
            "kind": "document_span",
            "document_version_id": str(upstream.document_version_id),
            "page": 1,
            "quote": "上游要求提供验收记录。",
        }
    ]
    candidate = ObligationCategoryCandidate.model_validate(raw_candidate)
    with pytest.raises(ValueError, match="obligation_absence_search_trace_missing"):
        bind_obligation_evidence(candidate, upstream=upstream, procurement=procurement)

    complete_trace = ObligationSearchTrace(
        category=candidate.category,
        document_version_id=procurement.document_version_id,
        searched_pages=(1, 2),
        searched_sections=("验收章节",),
        query_terms=("验收记录",),
    )
    bound = bind_obligation_evidence(
        candidate,
        upstream=upstream,
        procurement=procurement,
        search_traces=(complete_trace,),
    )
    absence_bound = bound[-1]
    assert absence_bound.parser_coverage == "complete"
    assert absence_bound.searched_pages == (1, 2)

    partial_trace = ObligationSearchTrace(
        category=candidate.category,
        document_version_id=procurement.document_version_id,
        searched_pages=(1,),
        searched_sections=("验收章节",),
        query_terms=("验收记录",),
    )
    with pytest.raises(ValueError, match="obligation_absence_source_incomplete"):
        bind_obligation_evidence(
            candidate,
            upstream=upstream,
            procurement=procurement,
            search_traces=(partial_trace,),
        )

    incomplete_procurement = _source(
        ("采购条款第一页。", "采购条款第二页。"),
        warnings=("partial",),
        document_version_id=procurement.document_version_id,
    )
    with pytest.raises(ValueError, match="obligation_absence_source_incomplete"):
        bind_obligation_evidence(
            candidate,
            upstream=upstream,
            procurement=incomplete_procurement,
            search_traces=(complete_trace,),
        )


def test_search_trace_copies_mutable_scope_inputs() -> None:
    pages = [1, 2]
    sections = ["验收章节"]
    query_terms = ["验收记录"]
    trace = ObligationSearchTrace(
        category=ObligationCategory.ACCEPTANCE_REMEDIATION,
        document_version_id=uuid4(),
        searched_pages=pages,
        searched_sections=sections,
        query_terms=query_terms,
    )

    pages[:] = [1]
    sections[0] = "调用方事后改写的章节"
    query_terms.clear()

    assert trace.searched_pages == (1, 2)
    assert trace.searched_sections == ("验收章节",)
    assert trace.query_terms == ("验收记录",)
    with pytest.raises(AttributeError):
        trace.searched_pages += (3,)  # type: ignore[misc]


def test_search_trace_rejects_boolean_and_malformed_page_numbers() -> None:
    trace_identity = {
        "category": ObligationCategory.ACCEPTANCE_REMEDIATION,
        "document_version_id": uuid4(),
        "searched_sections": ("验收章节",),
        "query_terms": ("验收记录",),
    }
    for pages in ((True, 2), "1", (0, 2), (1, 1)):
        with pytest.raises(ValueError, match="obligation_search_trace_incomplete"):
            ObligationSearchTrace(searched_pages=pages, **trace_identity)  # type: ignore[arg-type]


def test_search_trace_rejects_non_text_or_blank_search_terms() -> None:
    trace_identity = {
        "category": ObligationCategory.ACCEPTANCE_REMEDIATION,
        "document_version_id": uuid4(),
        "searched_pages": (1,),
        "searched_sections": ("验收章节",),
        "query_terms": ("验收记录",),
    }
    with pytest.raises(ValueError, match="obligation_search_trace_incomplete"):
        ObligationSearchTrace(**(trace_identity | {"query_terms": (object(),)}))  # type: ignore[arg-type]


def test_absence_search_traces_are_scoped_to_each_obligation_category() -> None:
    upstream = _source(("客户要求验收并提供审计材料。",))
    procurement = _source(("采购合同全文共两页。", "第二页也没有对应约定。"))
    categories = (
        ObligationCategory.ACCEPTANCE_REMEDIATION,
        ObligationCategory.DOCUMENTS_REPORTS_AUDIT,
    )
    candidates = []
    for category, query in zip(categories, ("验收整改", "报告审计"), strict=True):
        raw_candidate = _category_candidate(
            category,
            coverage="missing",
            procurement_evidence=[
                {
                    "side": "procurement",
                    "kind": "absence_evidence",
                    "document_version_id": str(procurement.document_version_id),
                    "searched_pages": [1, 2],
                    "searched_sections": ["全文"],
                    "query_terms": [query],
                }
            ],
        )
        raw_candidate["upstream_evidence"] = [
            {
                "side": "upstream",
                "kind": "document_span",
                "document_version_id": str(upstream.document_version_id),
                "page": 1,
                "quote": "客户要求验收并提供审计材料。",
            }
        ]
        candidates.append(ObligationCategoryCandidate.model_validate(raw_candidate))

    traces = tuple(
        ObligationSearchTrace(
            category=category,
            document_version_id=procurement.document_version_id,
            searched_pages=(1, 2),
            searched_sections=("全文",),
            query_terms=(query,),
        )
        for category, query in zip(categories, ("验收整改", "报告审计"), strict=True)
    )
    for candidate, query in zip(candidates, ("验收整改", "报告审计"), strict=True):
        bound = bind_obligation_evidence(
            candidate,
            upstream=upstream,
            procurement=procurement,
            search_traces=traces,
        )
        absence = next(item for item in bound if item.kind == "absence_evidence")
        assert absence.query_terms == (query,)
        assert absence.parser_coverage == "complete"

    with pytest.raises(ValueError, match="obligation_search_trace_duplicated"):
        bind_obligation_evidence(
            candidates[0],
            upstream=upstream,
            procurement=procurement,
            search_traces=(*traces, traces[0]),
        )


def test_absence_search_trace_cannot_be_reused_for_another_obligation_category() -> None:
    upstream = _source(("客户合同约定出具报告。",))
    procurement = _source(("采购合同未约定报告材料。",))
    raw_candidate = _category_candidate(
        ObligationCategory.DOCUMENTS_REPORTS_AUDIT,
        coverage="missing",
        procurement_evidence=[
            {
                "side": "procurement",
                "kind": "absence_evidence",
                "document_version_id": str(procurement.document_version_id),
                "searched_pages": [1],
                "searched_sections": ["全文"],
                "query_terms": ["报告材料"],
            }
        ],
    )
    raw_candidate["upstream_evidence"] = [
        {
            "side": "upstream",
            "kind": "document_span",
            "document_version_id": str(upstream.document_version_id),
            "page": 1,
            "quote": "客户合同约定出具报告。",
        }
    ]
    candidate = ObligationCategoryCandidate.model_validate(raw_candidate)
    unrelated_trace = ObligationSearchTrace(
        category=ObligationCategory.ACCEPTANCE_REMEDIATION,
        document_version_id=procurement.document_version_id,
        searched_pages=(1,),
        searched_sections=("全文",),
        query_terms=("报告材料",),
    )

    with pytest.raises(ValueError, match="obligation_absence_search_trace_missing"):
        bind_obligation_evidence(
            candidate,
            upstream=upstream,
            procurement=procurement,
            search_traces=(unrelated_trace,),
        )


def test_duplicate_or_unknown_category_rejected() -> None:
    one = _category_candidate(ObligationCategory.WORK_SCOPE_DELIVERABLES)
    with pytest.raises(ValidationError, match="duplicate_obligation_category"):
        ObligationCandidateOutput.model_validate({"categories": [one, one], "limitations": []})

    one["category"] = "made_up_category"
    with pytest.raises(ValidationError):
        ObligationCandidateOutput.model_validate({"categories": [one], "limitations": []})


def test_missing_model_categories_are_preserved_as_undetermined() -> None:
    provided = ObligationCandidateOutput.model_validate(
        {
            "categories": [
                _category_candidate(ObligationCategory.WORK_SCOPE_DELIVERABLES),
            ],
            "limitations": [],
        }
    )

    completed = complete_obligation_categories(provided)

    assert tuple(item.category for item in completed) == OBLIGATION_CATEGORY_ORDER
    assert completed[0].state == "candidate"
    assert all(item.state == "undetermined" for item in completed[1:])
    assert all(item.coverage is None for item in completed[1:])
    assert all(not item.upstream_evidence for item in completed[1:])


def test_binding_assembles_all_fourteen_categories_without_losing_bound_sides() -> None:
    upstream = _source(("双方约定具体交付期限。",))
    procurement = _source(("双方约定具体交付期限。",))
    candidate = _category_candidate(ObligationCategory.DELIVERY_SCHEDULE)
    candidate["upstream_evidence"][0]["document_version_id"] = str(upstream.document_version_id)
    candidate["procurement_evidence"][0]["document_version_id"] = str(
        procurement.document_version_id
    )
    output = ObligationCandidateOutput.model_validate(
        {"categories": [candidate], "limitations": ["模型返回的全局限制。"]}
    )

    bound = bind_obligation_candidate_output(
        output,
        upstream=upstream,
        procurement=procurement,
    )

    assert tuple(item.category for item in bound.categories) == OBLIGATION_CATEGORY_ORDER
    assert bound.limitations == ("模型返回的全局限制。",)
    delivery = next(
        item for item in bound.categories if item.category == ObligationCategory.DELIVERY_SCHEDULE
    )
    assert delivery.state == "candidate"
    assert [item.side for item in delivery.evidence] == ["upstream", "procurement"]
    assert {item.document_version_id for item in delivery.evidence} == {
        upstream.document_version_id,
        procurement.document_version_id,
    }
    assert all(
        item.state == "undetermined" and not item.evidence
        for item in bound.categories
        if item.category != ObligationCategory.DELIVERY_SCHEDULE
    )


def test_binding_rejects_the_whole_aggregate_when_any_category_has_a_bad_quote() -> None:
    upstream = _source(("双方约定具体交付期限。",))
    procurement = _source(("双方约定具体交付期限。",))
    valid = _category_candidate(ObligationCategory.WORK_SCOPE_DELIVERABLES)
    invalid = _category_candidate(ObligationCategory.DELIVERY_SCHEDULE)
    for candidate in (valid, invalid):
        candidate["upstream_evidence"][0]["document_version_id"] = str(upstream.document_version_id)
        candidate["procurement_evidence"][0]["document_version_id"] = str(
            procurement.document_version_id
        )
    invalid["procurement_evidence"][0]["quote"] = "来源中不存在的引文。"
    output = ObligationCandidateOutput.model_validate(
        {"categories": [valid, invalid], "limitations": []}
    )

    with pytest.raises(ValueError, match="obligation_evidence_quote_not_unique"):
        bind_obligation_candidate_output(
            output,
            upstream=upstream,
            procurement=procurement,
        )


def test_output_schema_is_closed_and_caps_categories_at_fourteen() -> None:
    schema = obligation_candidate_output_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["categories"]["maxItems"] == 14
    assert schema["$defs"]["ObligationCategory"]["enum"] == [
        category.value for category in OBLIGATION_CATEGORY_ORDER
    ]
