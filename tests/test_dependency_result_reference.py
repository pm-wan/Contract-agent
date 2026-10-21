import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

import pytest

from contract_app.workflow.dependency_result_reference import (
    DependencyResultReferenceInvalid,
    resolve_dependency_result_reference,
)
from contract_app.workflow.job_store import RunResultsView, RunView
from contract_app.workflow.rule_plan_execution import RulePlanExecutionSnapshot

RULE_ID = "legal.price.definition"
RULE_VERSION = "1.0.0"
RULE_SHA256 = "a" * 64
CASE_ID = uuid4()
INPUT_SNAPSHOT_ID = uuid4()
SOURCE_RUN_ID = uuid4()
RESULT_ID = uuid4()
FACT_SET_SNAPSHOT_ID = uuid4()
RULE_MANIFEST = {
    "rule": {
        "rule_id": RULE_ID,
        "version": RULE_VERSION,
        "source_sha256": RULE_SHA256,
    }
}
RULE_MANIFEST_SHA256 = sha256(
    json.dumps(RULE_MANIFEST, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
).hexdigest()


def _view(
    *,
    case_id: UUID = CASE_ID,
    input_snapshot_id: UUID = INPUT_SNAPSHOT_ID,
    fact_set_snapshot_id: UUID | None = None,
    status: str = "completed",
    result_count: int = 1,
) -> RunResultsView:
    now = datetime.now(UTC)
    snapshot = RulePlanExecutionSnapshot.capture(
        input_snapshot_id=input_snapshot_id,
        fact_set_snapshot_id=fact_set_snapshot_id,
        rule_id=RULE_ID,
        rule_version=RULE_VERSION,
        rule_source_sha256=RULE_SHA256,
        plan_sha256="b" * 64,
        facts_sha256="c" * 64,
        assessment_status="blocked",
        blocked_by_checks=("legal.price.definition",),
        missing_slots=(),
        unresolved_minimum_judgment_indexes=(),
    )
    result = {
        "result_id": str(RESULT_ID),
        "task_result_sha256": "d" * 64,
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "judgment": "undetermined",
        "evidence": [
            {
                "evidence_id": str(uuid4()),
                "kind": "document_span",
                "support": "context",
                "source_version": "f" * 64,
                "document_version_id": str(uuid4()),
                "page": 1,
                "start_offset": 0,
                "end_offset": 4,
                "text_hash": "1" * 64,
                "quote": "测试原文",
            }
        ],
        "fact_set_snapshot_id": (
            str(fact_set_snapshot_id) if fact_set_snapshot_id is not None else None
        ),
        "rule_plan_execution": snapshot.model_dump(mode="json"),
        "definition_manifest": {**RULE_MANIFEST, "manifest_sha256": RULE_MANIFEST_SHA256},
    }
    return RunResultsView(
        run=RunView(SOURCE_RUN_ID, case_id, input_snapshot_id, status, 1, now),
        results=tuple(dict(result) for _ in range(result_count)),
    )


def _view_with_dependency_rule(*, check_id: str = RULE_ID) -> RunResultsView:
    dependency_check_id = RULE_ID if check_id == RULE_ID else "other.check"
    definitions = [
        {
            "check_id": "legal.payment_conditions",
            "rule_id": "legal.payment_conditions",
            "rule_version": "1.0.0",
            "rule_sha256": "9" * 64,
            "dependencies": [RULE_ID],
        },
        {
            "check_id": dependency_check_id,
            "rule_id": RULE_ID,
            "rule_version": RULE_VERSION,
            "rule_sha256": RULE_SHA256,
            "dependencies": [],
        },
    ]
    graph_body = {
        "definitions": definitions,
        "execution_order": [dependency_check_id, "legal.payment_conditions"],
        "blockers": [],
    }
    manifest = {
        "rule": {
            "rule_id": "legal.payment_conditions",
            "version": "1.0.0",
            "source_sha256": "9" * 64,
        },
        "dependency_graph": {
            "kind": "development_rule_dependency_graph",
            "status": "graph_resolved",
            **graph_body,
            "graph_sha256": sha256(
                json.dumps(graph_body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "is_execution_proof": False,
            "is_approval_record": False,
        },
    }
    manifest["manifest_sha256"] = sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    view = _view()
    result = dict(view.results[0])
    result["definition_manifest"] = manifest
    return RunResultsView(view.run, (result,))


def test_reference_binds_exact_persisted_result_without_completing_dependency() -> None:
    reference = resolve_dependency_result_reference(
        _view(),
        expected_case_id=CASE_ID,
        expected_input_snapshot_id=INPUT_SNAPSHOT_ID,
        expected_fact_set_snapshot_id=None,
        check_id="legal.price.definition",
        dependency_rule_id=RULE_ID,
        dependency_rule_version=RULE_VERSION,
        dependency_rule_sha256=RULE_SHA256,
    )

    assert reference.case_id == CASE_ID
    assert reference.input_snapshot_id == INPUT_SNAPSHOT_ID
    assert reference.fact_set_snapshot_id is None
    assert reference.source_run_id == SOURCE_RUN_ID
    assert reference.result_id == RESULT_ID
    assert reference.task_result_sha256 == "d" * 64
    assert reference.observed_judgment == "undetermined"
    assert reference.assessment_status == "blocked"
    assert reference.definition_manifest_sha256 == RULE_MANIFEST_SHA256
    assert reference.is_completion_proof is False


def test_reference_resolves_dependency_rule_from_frozen_graph_mapping() -> None:
    reference = resolve_dependency_result_reference(
        _view_with_dependency_rule(),
        expected_case_id=CASE_ID,
        expected_input_snapshot_id=INPUT_SNAPSHOT_ID,
        expected_fact_set_snapshot_id=None,
        check_id=RULE_ID,
        dependency_rule_id=RULE_ID,
        dependency_rule_version=RULE_VERSION,
        dependency_rule_sha256=RULE_SHA256,
    )

    assert reference.check_id == RULE_ID
    assert reference.dependency_rule_id == RULE_ID
    assert reference.is_completion_proof is False


def test_reference_rejects_check_id_mapped_to_a_different_graph_node() -> None:
    with pytest.raises(DependencyResultReferenceInvalid, match="dependency_result_missing"):
        resolve_dependency_result_reference(
            _view_with_dependency_rule(check_id="other.check"),
            expected_case_id=CASE_ID,
            expected_input_snapshot_id=INPUT_SNAPSHOT_ID,
            expected_fact_set_snapshot_id=None,
            check_id=RULE_ID,
            dependency_rule_id=RULE_ID,
            dependency_rule_version=RULE_VERSION,
            dependency_rule_sha256=RULE_SHA256,
        )


@pytest.mark.parametrize(
    ("view_options", "expected_case_id", "expected_input_snapshot_id"),
    [
        ({"case_id": uuid4()}, CASE_ID, INPUT_SNAPSHOT_ID),
        ({"input_snapshot_id": uuid4()}, CASE_ID, INPUT_SNAPSHOT_ID),
        ({"status": "failed"}, CASE_ID, INPUT_SNAPSHOT_ID),
    ],
)
def test_reference_rejects_other_case_input_or_noncompleted_run(
    view_options: dict[str, object],
    expected_case_id: UUID,
    expected_input_snapshot_id: UUID,
) -> None:
    view = _view(
        case_id=cast(UUID, view_options.get("case_id", CASE_ID)),
        input_snapshot_id=cast(UUID, view_options.get("input_snapshot_id", INPUT_SNAPSHOT_ID)),
        status=cast(str, view_options.get("status", "completed")),
    )
    with pytest.raises(
        DependencyResultReferenceInvalid, match="dependency_result_run_binding_invalid"
    ):
        resolve_dependency_result_reference(
            view,
            expected_case_id=expected_case_id,
            expected_input_snapshot_id=expected_input_snapshot_id,
            expected_fact_set_snapshot_id=None,
            check_id="legal.price.definition",
            dependency_rule_id=RULE_ID,
            dependency_rule_version=RULE_VERSION,
            dependency_rule_sha256=RULE_SHA256,
        )


def test_reference_rejects_ambiguous_matching_results() -> None:
    with pytest.raises(DependencyResultReferenceInvalid, match="dependency_result_ambiguous"):
        resolve_dependency_result_reference(
            _view(result_count=2),
            expected_case_id=CASE_ID,
            expected_input_snapshot_id=INPUT_SNAPSHOT_ID,
            expected_fact_set_snapshot_id=None,
            check_id="legal.price.definition",
            dependency_rule_id=RULE_ID,
            dependency_rule_version=RULE_VERSION,
            dependency_rule_sha256=RULE_SHA256,
        )


def test_reference_rejects_a_result_from_an_old_fact_set_snapshot() -> None:
    with pytest.raises(
        DependencyResultReferenceInvalid, match="dependency_result_snapshot_binding_invalid"
    ):
        resolve_dependency_result_reference(
            _view(fact_set_snapshot_id=FACT_SET_SNAPSHOT_ID),
            expected_case_id=CASE_ID,
            expected_input_snapshot_id=INPUT_SNAPSHOT_ID,
            expected_fact_set_snapshot_id=None,
            check_id="legal.price.definition",
            dependency_rule_id=RULE_ID,
            dependency_rule_version=RULE_VERSION,
            dependency_rule_sha256=RULE_SHA256,
        )


def test_reference_rejects_missing_dependency_rule_result() -> None:
    with pytest.raises(DependencyResultReferenceInvalid, match="dependency_result_missing"):
        resolve_dependency_result_reference(
            _view(),
            expected_case_id=CASE_ID,
            expected_input_snapshot_id=INPUT_SNAPSHOT_ID,
            expected_fact_set_snapshot_id=None,
            check_id="legal.price.definition",
            dependency_rule_id="legal.other_rule",
            dependency_rule_version=RULE_VERSION,
            dependency_rule_sha256=RULE_SHA256,
        )


def test_reference_rejects_an_unverifiable_result_hash() -> None:
    view = _view()
    result = dict(view.results[0])
    result["task_result_sha256"] = "invalid"
    view = RunResultsView(view.run, (result,))

    with pytest.raises(
        DependencyResultReferenceInvalid, match="dependency_result_snapshot_binding_invalid"
    ):
        resolve_dependency_result_reference(
            view,
            expected_case_id=CASE_ID,
            expected_input_snapshot_id=INPUT_SNAPSHOT_ID,
            expected_fact_set_snapshot_id=None,
            check_id="legal.price.definition",
            dependency_rule_id=RULE_ID,
            dependency_rule_version=RULE_VERSION,
            dependency_rule_sha256=RULE_SHA256,
        )


def test_reference_rejects_a_tampered_definition_manifest() -> None:
    view = _view()
    result = dict(view.results[0])
    manifest = dict(result["definition_manifest"])
    manifest["tampered_field"] = "not bound to the manifest hash"
    result["definition_manifest"] = manifest
    view = RunResultsView(view.run, (result,))

    with pytest.raises(
        DependencyResultReferenceInvalid, match="dependency_result_manifest_hash_mismatch"
    ):
        resolve_dependency_result_reference(
            view,
            expected_case_id=CASE_ID,
            expected_input_snapshot_id=INPUT_SNAPSHOT_ID,
            expected_fact_set_snapshot_id=None,
            check_id="legal.price.definition",
            dependency_rule_id=RULE_ID,
            dependency_rule_version=RULE_VERSION,
            dependency_rule_sha256=RULE_SHA256,
        )
