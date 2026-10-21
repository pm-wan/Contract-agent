import os
from uuid import uuid4

import psycopg
import pytest

from contract_app.workflow.execution_trace import ExecutionTraceRecorder
from contract_app.workflow.job_store import JobStore, _summarize_run_model_usage

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


def test_claim_fence_result_outbox_and_tenant_isolation() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant_a, tenant_b = uuid4(), uuid4()
    _, run_a = store.create_case_and_run(tenant_a, "M0 contract")
    task_id = store.enqueue(tenant_a, run_a, "parse_document", "same-submit", {"document": "x"})
    assert (
        store.enqueue(tenant_a, run_a, "parse_document", "same-submit", {"document": "x"})
        == task_id
    )
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        store.enqueue(tenant_a, run_a, "parse_document", "same-submit", {"document": "y"})
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_a),))
        connection.execute(
            "UPDATE tasks SET created_at = now() - interval '2 seconds', "
            "next_run_at = now() - interval '2 seconds' WHERE id = %s",
            (task_id,),
        )
    assert store.claim(tenant_b, "other-worker") is None
    first = store.claim(tenant_a, "worker-1")
    assert first is not None and first.id == task_id and first.fencing_token == 1
    assert first.queue_wait_ms >= 1500
    assert store.claim(tenant_a, "worker-2") is None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_a),))
        connection.execute(
            "UPDATE tasks SET lease_until = now() - interval '1 second' WHERE id = %s",
            (task_id,),
        )
    second = store.claim(tenant_a, "worker-2")
    assert second is not None and second.fencing_token == 2 and second.attempt == 2
    assert second.queue_wait_ms >= 500
    assert not store.complete(tenant_a, first, "worker-1", {"ok": True})
    assert store.complete(tenant_a, second, "worker-2", {"ok": True})
    assert not store.complete(tenant_a, second, "worker-2", {"ok": True})
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        assert connection.execute("SELECT count(*) FROM task_results").fetchone()[0] == 0
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_a),))
        assert connection.execute("SELECT count(*) FROM task_results").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM outbox_events").fetchone()[0] == 1
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_b),))
        assert connection.execute("SELECT count(*) FROM task_results").fetchone()[0] == 0


def test_cancel_invalidates_lease() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant = uuid4()
    _, run_id = store.create_case_and_run(tenant, "cancelled")
    task_id = store.enqueue(tenant, run_id, "parse_document", "cancel-me", {})
    task = store.claim(tenant, "worker")
    assert task is not None
    assert store.cancel(tenant, task_id)
    assert not store.complete(tenant, task, "worker", {"ok": True})
    assert store.claim(tenant, "next-worker") is None


def test_retry_returns_task_to_queue() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant = uuid4()
    _, run_id = store.create_case_and_run(tenant, "retry")
    store.enqueue(tenant, run_id, "parse_document", "retry-me", {})
    task = store.claim(tenant, "worker-1")
    assert task is not None
    assert store.retry(tenant, task, "worker-1", "temporary", 0)
    retry = store.claim(tenant, "worker-2")
    assert retry is not None and retry.attempt == 2


def test_execution_trace_events_are_attempt_scoped_fenced_and_append_only() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant, other_tenant = uuid4(), uuid4()
    _, run_id = store.create_case_and_run(tenant, "trace attempts")
    task_id = store.enqueue(tenant, run_id, "contract_review", "trace-attempts", {})
    first = store.claim(tenant, "worker-1")
    assert first is not None
    first_events = ExecutionTraceRecorder(
        event_sink=lambda event: store.record_execution_trace_event(tenant, first, event)
    )
    first_events.record_payload("model_request", {"messages": []})
    first_response = first_events.record_payload(
        "model_response", {"status": "ok"}, input_tokens=40, output_tokens=7
    )
    assert store.record_execution_trace_event(tenant, first, first_response)

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE tasks SET lease_until = now() - interval '1 second' WHERE id = %s",
            (task_id,),
        )
    second = store.claim(tenant, "worker-2")
    assert second is not None and second.attempt == 2
    assert not store.record_execution_trace_event(tenant, first, first_response)

    second_events = ExecutionTraceRecorder(
        event_sink=lambda event: store.record_execution_trace_event(tenant, second, event)
    )
    second_events.record_payload("model_request", {"messages": []})
    second_events.record_payload(
        "model_response", {"status": "ok"}, input_tokens=60, output_tokens=9
    )
    second_task_id = store.enqueue(tenant, run_id, "contract_review", "trace-unanswered", {})
    interrupted = store.claim(tenant, "worker-3")
    assert interrupted is not None and interrupted.id == second_task_id
    interrupted_events = ExecutionTraceRecorder(
        event_sink=lambda event: store.record_execution_trace_event(tenant, interrupted, event)
    )
    interrupted_events.record_payload("model_request", {"messages": []})
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        grants = connection.execute(
            "SELECT has_table_privilege(current_user, "
            "'task_execution_trace_events', 'UPDATE'), "
            "has_table_privilege(current_user, 'task_execution_trace_events', 'DELETE')"
        ).fetchone()
        assert grants == (False, False)
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT attempt, count(*), sum(input_tokens)::bigint, sum(output_tokens)::bigint "
            "FROM task_execution_trace_events WHERE task_id = %s "
            "GROUP BY attempt ORDER BY attempt",
            (task_id,),
        ).fetchall() == [(1, 2, 40, 7), (2, 2, 60, 9)]
        event_rows = connection.execute(
            "SELECT task_id, attempt, sequence, event_type, payload_sha256, payload_bytes, "
            "previous_event_sha256, event_sha256, input_tokens, output_tokens "
            "FROM task_execution_trace_events WHERE run_id = %s "
            "ORDER BY task_id, attempt, sequence",
            (run_id,),
        ).fetchall()
        event_fields = (
            "task_id",
            "attempt",
            "sequence",
            "event_type",
            "payload_sha256",
            "payload_bytes",
            "previous_event_sha256",
            "event_sha256",
            "input_tokens",
            "output_tokens",
        )
        events = [dict(zip(event_fields, row, strict=True)) for row in event_rows]
        usage = _summarize_run_model_usage(events)
        assert usage is not None
        assert usage.model_requests == 3
        assert usage.model_responses == 2
        assert usage.usage_reports == 2
        assert (usage.input_tokens, usage.output_tokens, usage.complete) == (100, 16, False)
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(other_tenant),))
        assert connection.execute(
            "SELECT count(*) FROM task_execution_trace_events WHERE task_id = %s", (task_id,)
        ).fetchone() == (0,)


def test_heartbeat_cannot_revive_expired_or_cancelled_lease() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant = uuid4()
    _, run_id = store.create_case_and_run(tenant, "heartbeat")
    task_id = store.enqueue(tenant, run_id, "parse_document", "heartbeat", {})
    task = store.claim(tenant, "worker", lease_seconds=1)
    assert task is not None
    assert store.heartbeat(tenant, task, "worker", lease_seconds=1)
    assert not store.heartbeat(tenant, task, "wrong-worker", lease_seconds=1)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE tasks SET lease_until = now() - interval '1 second' WHERE id = %s",
            (task_id,),
        )
    assert not store.heartbeat(tenant, task, "worker", lease_seconds=1)
    successor = store.claim(tenant, "successor")
    assert successor is not None
    assert not store.heartbeat(tenant, task, "worker", lease_seconds=1)
    assert store.cancel(tenant, task_id)
    assert not store.heartbeat(tenant, successor, "successor", lease_seconds=60)


def test_result_insert_failure_rolls_back_task_completion_and_outbox() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant = uuid4()
    _, run_id = store.create_case_and_run(tenant, "atomic completion")
    task_id = store.enqueue(tenant, run_id, "parse_document", "atomic", {})
    task = store.claim(tenant, "worker")
    assert task is not None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "INSERT INTO task_results (tenant_id, task_id, run_id, result, content_hash) "
            "VALUES (%s, %s, %s, '{}'::jsonb, %s)",
            (tenant, task_id, run_id, "0" * 64),
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.complete(tenant, task, "worker", {"ok": True})
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status FROM tasks WHERE id = %s", (task_id,)
        ).fetchone() == ("running",)
        assert connection.execute(
            "SELECT count(*) FROM outbox_events WHERE task_id = %s", (task_id,)
        ).fetchone() == (0,)


@pytest.mark.parametrize("end_state", ["cancelled", "superseded"])
def test_run_termination_invalidates_leases_and_rejects_new_tasks(end_state: str) -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant, outsider = uuid4(), uuid4()
    _, run_id = store.create_case_and_run(tenant, "run termination")
    running_id = store.enqueue(tenant, run_id, "extract", "running", {})
    queued_id = store.enqueue(tenant, run_id, "extract", "queued", {})
    running = store.claim(tenant, "worker")
    assert running is not None and running.id == running_id
    assert not store.cancel_run(outsider, run_id)
    end_run = store.cancel_run if end_state == "cancelled" else store.supersede_run
    assert end_run(tenant, run_id)
    assert end_run(tenant, run_id)
    assert not (store.supersede_run if end_state == "cancelled" else store.cancel_run)(
        tenant, run_id
    )
    assert not store.heartbeat(tenant, running, "worker", 60)
    assert not store.complete(tenant, running, "worker", {"ok": True})
    assert not store.retry(tenant, running, "worker", "test", 0)
    assert not store.fail(tenant, running, "worker", "test")
    assert store.claim(tenant, "next-worker") is None
    with pytest.raises(ValueError, match="run_not_active"):
        store.enqueue(tenant, run_id, "extract", "new-task", {})
    with pytest.raises(ValueError, match="run_not_active"):
        store.enqueue(tenant, run_id, "extract", "queued", {})
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status, version FROM audit_runs WHERE id = %s", (run_id,)
        ).fetchone() == (end_state, 2)
        assert connection.execute(
            "SELECT status, lease_owner, lease_until, fencing_token FROM tasks WHERE id = %s",
            (running_id,),
        ).fetchone() == (end_state, None, None, 2)
        assert connection.execute(
            "SELECT status FROM tasks WHERE id = %s", (queued_id,)
        ).fetchone() == (end_state,)
        assert connection.execute(
            "SELECT count(*) FROM task_results WHERE run_id = %s", (run_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM outbox_events WHERE run_id = %s", (run_id,)
        ).fetchone() == (0,)


def test_run_termination_keeps_already_committed_results() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant = uuid4()
    _, run_id = store.create_case_and_run(tenant, "keep history")
    completed_id = store.enqueue(tenant, run_id, "extract", "completed", {})
    task = store.claim(tenant, "worker")
    assert task is not None
    assert store.complete(tenant, task, "worker", {"ok": True})
    assert store.supersede_run(tenant, run_id)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status FROM tasks WHERE id = %s", (completed_id,)
        ).fetchone() == ("completed",)
        assert connection.execute(
            "SELECT count(*) FROM task_results WHERE task_id = %s", (completed_id,)
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT status FROM audit_runs WHERE id = %s", (run_id,)
        ).fetchone() == ("superseded",)
