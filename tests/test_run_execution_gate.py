from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from contract_app.api import create_app
from contract_app.api_runs import FrozenDefinitionSnapshot, get_job_store
from contract_app.api_snapshots import Principal, require_principal


@pytest.mark.parametrize(
    ("environment", "enabled", "expected"),
    [
        ("development", "true", 202),
        ("test", "true", 202),
        ("production", "true", 503),
        ("development", "false", 503),
        ("test", "false", 503),
        ("", "true", 503),
    ],
)
def test_review_execution_requires_enabled_local_environment(
    monkeypatch: pytest.MonkeyPatch, environment: str, enabled: str, expected: int
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("CONTRACT_REVIEW_EXECUTION_ENABLED", enabled)
    monkeypatch.setenv("CONTRACT_REVIEW_MODEL", "offline-mock")
    monkeypatch.setattr(
        FrozenDefinitionSnapshot,
        "capture",
        lambda *_: SimpleNamespace(storage_value=lambda: {"test": "frozen-definition"}),
    )
    case_id, snapshot_id, run_id = uuid4(), uuid4(), uuid4()
    jobs = Mock()
    jobs.start_bound_run.return_value = run_id
    jobs.get_run.return_value = SimpleNamespace(
        id=run_id,
        case_id=case_id,
        input_snapshot_id=snapshot_id,
        status="queued",
        version=1,
        created_at=datetime.now(UTC),
    )
    app = create_app()
    principal = Principal(uuid4(), uuid4())
    app.dependency_overrides[require_principal] = lambda: principal
    app.dependency_overrides[get_job_store] = lambda: jobs
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/cases/{case_id}/runs",
            json={"input_snapshot_id": str(snapshot_id)},
            headers={"Idempotency-Key": "main-contract-review"},
        )
    assert response.status_code == expected, response.text
    if expected == 202:
        assert response.json()["run_id"] == str(run_id)
        assert jobs.start_bound_run.call_args.args[:5] == (
            principal.tenant_id,
            case_id,
            principal.actor_id,
            snapshot_id,
            "main-contract-review",
        )
    else:
        assert response.json()["code"] == "review_execution_disabled"
        jobs.start_bound_run.assert_not_called()
