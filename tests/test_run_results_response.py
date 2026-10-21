from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from contract_app.api_runs import (
    CheckResultResponse,
    DependencyGraphRuleReference,
    DevelopmentDependencyGraph,
    ExecutionDefinitionManifest,
    RuleDefinitionReference,
    RunResponse,
    RunResultsResponse,
    _enforce_run_results_response_limit,
    get_job_store,
    router,
)
from contract_app.api_snapshots import Principal, require_principal
from contract_app.workflow.job_store import RunResultsView, RunView
from contract_app.workflow.rule_plan_execution import RulePlanExecutionSnapshot


def _response(summary: str | None = None) -> RunResultsResponse:
    result: list[CheckResultResponse] = []
    if summary is not None:
        result.append(
            CheckResultResponse.model_validate(
                {
                    "result_id": uuid4(),
                    "task_result_sha256": "d" * 64,
                    "rule_id": "legal.payment_conditions",
                    "rule_version": "1.0.0",
                    "judgment": "undetermined",
                    "severity": "undetermined",
                    "summary": summary,
                    "evidence": [],
                    "limitations": [],
                    "created_at": datetime.now(UTC),
                }
            )
        )
    return RunResultsResponse(
        run=RunResponse(
            run_id=uuid4(),
            case_id=uuid4(),
            input_snapshot_id=uuid4(),
            status="completed",
            version=1,
            created_at=datetime.now(UTC),
        ),
        results=result,
    )


def test_run_results_response_within_byte_limit_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("contract_app.api_runs.MAX_RUN_RESULTS_RESPONSE_BYTES", 4096)
    response = _response("有限的报告内容")

    assert _enforce_run_results_response_limit(response) is response


def test_oversized_run_results_response_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contract_app.api_runs.MAX_RUN_RESULTS_RESPONSE_BYTES", 128)
    response = _response("内容" * 100)

    with pytest.raises(HTTPException) as error:
        _enforce_run_results_response_limit(response)

    assert error.value.status_code == 503
    assert error.value.detail == "service_unavailable"


def test_results_route_returns_503_instead_of_a_partial_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("contract_app.api_runs.MAX_RUN_RESULTS_RESPONSE_BYTES", 128)
    now = datetime.now(UTC)
    run_id = uuid4()
    run = RunView(uuid4(), uuid4(), uuid4(), "completed", 1, now)
    stored_result = {
        "result_id": str(uuid4()),
        "rule_id": "legal.payment_conditions",
        "rule_version": "1.0.0",
        "judgment": "undetermined",
        "severity": "undetermined",
        "summary": "超限内容" * 100,
        "evidence": [],
        "limitations": [],
        "created_at": now.isoformat(),
    }

    class FakeJobStore:
        def get_run_results(
            self, tenant_id: UUID, actor_id: UUID, requested_run_id: UUID
        ) -> RunResultsView:
            assert requested_run_id == run_id
            return RunResultsView(run=run, results=(stored_result,))

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[require_principal] = lambda: Principal(uuid4(), uuid4())
    test_app.dependency_overrides[get_job_store] = FakeJobStore

    with TestClient(test_app) as client:
        response = client.get(f"/api/v1/runs/{run_id}/results")

    assert response.status_code == 503
    assert response.json()["detail"] == "service_unavailable"


def test_run_results_reject_plan_assessment_bound_to_another_input() -> None:
    now = datetime.now(UTC)
    input_snapshot_id = uuid4()
    snapshot = RulePlanExecutionSnapshot.capture(
        input_snapshot_id=uuid4(),
        fact_set_snapshot_id=None,
        rule_id="legal.payment_conditions",
        rule_version="1.0.0",
        rule_source_sha256="a" * 64,
        plan_sha256="b" * 64,
        facts_sha256="c" * 64,
        assessment_status="blocked",
        blocked_by_checks=("legal.price.definition",),
        missing_slots=(),
        unresolved_minimum_judgment_indexes=(),
    )
    result = CheckResultResponse(
        result_id=uuid4(),
        task_result_sha256="d" * 64,
        rule_id="legal.payment_conditions",
        rule_version="1.0.0",
        judgment="undetermined",
        severity="undetermined",
        evidence=[],
        limitations=[],
        created_at=now,
        rule_plan_execution=snapshot,
    )

    with pytest.raises(ValueError, match="rule_plan_execution_binding_invalid"):
        RunResultsResponse(
            run=RunResponse(
                run_id=uuid4(),
                case_id=uuid4(),
                input_snapshot_id=input_snapshot_id,
                status="completed",
                version=1,
                created_at=now,
            ),
            results=[result],
        )


def test_run_results_accepts_plan_bound_to_a_rule_in_its_frozen_dependency_graph() -> None:
    now = datetime.now(UTC)
    input_snapshot_id = uuid4()
    dependency_source_sha256 = "f" * 64
    snapshot = RulePlanExecutionSnapshot.capture(
        input_snapshot_id=input_snapshot_id,
        fact_set_snapshot_id=None,
        rule_id="legal.price.definition",
        rule_version="1.0.0",
        rule_source_sha256=dependency_source_sha256,
        plan_sha256="b" * 64,
        facts_sha256="c" * 64,
        assessment_status="undetermined",
        blocked_by_checks=(),
        missing_slots=("price_basis",),
        unresolved_minimum_judgment_indexes=(),
    )
    manifest = ExecutionDefinitionManifest.model_construct(
        rule=RuleDefinitionReference.model_construct(
            source_path="payment.yaml",
            source_sha256="a" * 64,
            rule_id="legal.payment_conditions",
            version="1.0.0",
        ),
        dependency_graph=DevelopmentDependencyGraph.model_construct(
            definitions=[
                DependencyGraphRuleReference.model_construct(
                    check_id="legal.price.definition",
                    rule_id="legal.price.definition",
                    rule_version="1.0.0",
                    rule_sha256=dependency_source_sha256,
                    dependencies=[],
                )
            ]
        ),
    )
    result = CheckResultResponse.model_construct(
        result_id=uuid4(),
        task_result_sha256="d" * 64,
        rule_id="legal.price.definition",
        rule_version="1.0.0",
        judgment="undetermined",
        severity="undetermined",
        evidence=[],
        limitations=[],
        created_at=now,
        definition_manifest=manifest,
        rule_plan_execution=snapshot,
    )

    response = RunResultsResponse(
        run=RunResponse(
            run_id=uuid4(),
            case_id=uuid4(),
            input_snapshot_id=input_snapshot_id,
            status="completed",
            version=1,
            created_at=now,
        ),
        results=[result],
    )

    assert response.results[0].rule_plan_execution == snapshot
