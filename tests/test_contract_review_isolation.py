from threading import Event
from typing import Any
from uuid import uuid4

import pytest

from contract_app.workflow import contract_review
from contract_app.workflow.job_store import LeasedTask
from contract_app.workflow.worker import WorkerTaskContext


def test_review_handler_routes_only_nonsecret_inputs_through_process_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    task = LeasedTask(
        id=uuid4(),
        run_id=uuid4(),
        task_type="contract_review",
        payload={"frozen_input_snapshot_id": str(uuid4())},
        attempt=1,
        fencing_token=4,
        lease_owner="review-worker",
        traceparent=None,
    )
    payload = {"snapshot_id": task.payload["frozen_input_snapshot_id"]}
    profile: dict[str, Any] = {"model": "test-model", "endpoint_sha256": "test-digest"}
    worker_cancellation = Event()
    received: dict[str, Any] = {}

    def fake_run(
        target: Any,
        args: tuple[Any, ...],
        *,
        cancellation: Any,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        received.update(
            target=target,
            args=args,
            cancellation=cancellation,
            timeout_seconds=timeout_seconds,
        )
        return {"isolated": True}

    monkeypatch.setattr(contract_review, "run_cancellable_process", fake_run)
    handler = contract_review.IsolatedContractReviewHandler(
        tenant_id,
        "test-pageindex-model",
        profile,
        timeout_seconds=12,
        process_timeout_seconds=40,
    )
    result = handler(payload, task, WorkerTaskContext(worker_cancellation))

    assert result == {"isolated": True}
    assert received == {
        "target": contract_review._run_contract_review_in_child,
        "args": (
            tenant_id,
            "test-pageindex-model",
            profile,
            12,
            3,
            payload,
            task,
        ),
        "cancellation": worker_cancellation,
        "timeout_seconds": 40,
    }
