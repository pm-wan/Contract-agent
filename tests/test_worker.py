import multiprocessing
import os
import time
from multiprocessing.connection import Connection
from threading import Event, Thread
from uuid import UUID, uuid4

import psycopg
import pytest

from contract_app.workflow.job_store import JobStore, LeasedTask
from contract_app.workflow.worker import Worker, WorkerTaskContext

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


def _run_probe_worker(
    database_url: str,
    tenant_id: UUID,
    worker_id: str,
    ready: Connection,
    block: bool,
) -> None:
    def echo(payload: dict[str, object]) -> dict[str, object]:
        ready.send("claimed")
        if block:
            time.sleep(30)
        return {"echo": payload["value"]}

    worker = Worker(JobStore(database_url), tenant_id, worker_id, {"echo": echo}, lease_seconds=1)
    assert worker.run_once() == "completed"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process test requires POSIX fork")
def test_killed_worker_reclaims_task_without_duplicate_result() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant_id = uuid4()
    _, run_id = store.create_case_and_run(tenant_id, "worker restart")
    task_id = store.enqueue(tenant_id, run_id, "echo", "restart-test", {"value": "safe"})

    context = multiprocessing.get_context("fork")
    first_read, first_write = context.Pipe(duplex=False)
    first = context.Process(
        target=_run_probe_worker,
        args=(TEST_DATABASE_URL, tenant_id, "worker-before-crash", first_write, True),
    )
    second: multiprocessing.Process | None = None
    first.start()
    try:
        first_write.close()
        assert first_read.poll(10), "first worker did not claim task"
        assert first_read.recv() == "claimed"
        first.terminate()
        first.join(10)
        assert first.exitcode is not None and first.exitcode != 0
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            assert connection.execute(
                "SELECT status FROM audit_runs WHERE id = %s", (run_id,)
            ).fetchone() == ("running",)
        time.sleep(1.2)
        second_read, second_write = context.Pipe(duplex=False)
        second = context.Process(
            target=_run_probe_worker,
            args=(TEST_DATABASE_URL, tenant_id, "worker-after-restart", second_write, False),
        )
        second.start()
        second_write.close()
        second.join(10)
        assert second.exitcode == 0
        assert second_read.poll(1) and second_read.recv() == "claimed"
        second_read.close()
    finally:
        first_read.close()
        if first.is_alive():
            first.terminate()
            first.join(10)
        if second is not None and second.is_alive():
            second.terminate()
            second.join(10)

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        row = connection.execute(
            "SELECT status, attempt, fencing_token FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        assert row == ("completed", 2, 2)
        assert connection.execute(
            "SELECT count(*) FROM task_results WHERE task_id = %s", (task_id,)
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM outbox_events WHERE task_id = %s", (task_id,)
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT status FROM audit_runs WHERE id = %s", (run_id,)
        ).fetchone() == ("completed",)


def test_worker_retries_then_fails_with_sanitized_error() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant_id = uuid4()
    _, run_id = store.create_case_and_run(tenant_id, "retry budget")
    task_id = store.enqueue(tenant_id, run_id, "extract", "bad-input", {"secret": "hidden"})

    def broken(_payload: dict[str, object]) -> dict[str, object]:
        raise ValueError("contract text must not be stored in last_error")

    worker = Worker(
        store,
        tenant_id,
        "limited-worker",
        {"extract": broken},
        max_attempts=2,
        retry_delay_seconds=0,
    )
    assert worker.run_once() == "retried"
    assert worker.run_once() == "failed"
    assert worker.run_once() == "idle"

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        row = connection.execute(
            "SELECT status, attempt, last_error FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        assert row == ("failed", 2, "ValueError")
        assert connection.execute(
            "SELECT status FROM audit_runs WHERE id = %s", (run_id,)
        ).fetchone() == ("failed",)
        event = connection.execute(
            "SELECT event_type, payload FROM outbox_events WHERE task_id = %s", (task_id,)
        ).fetchone()
        assert event is not None and event[0] == "task.failed"
        assert event[1]["error_code"] == "ValueError"
        assert "contract text" not in str(event[1])


def test_runtime_role_cannot_rewrite_or_delete_committed_results() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant_id = uuid4()
    _, run_id = store.create_case_and_run(tenant_id, "immutable result")
    task_id = store.enqueue(tenant_id, run_id, "extract", "immutable-result", {})
    worker = Worker(store, tenant_id, "result-writer", {"extract": lambda _payload: {"ok": True}})
    assert worker.run_once() == "completed"

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        privileges = connection.execute(
            "SELECT has_table_privilege(current_user, 'task_results', 'UPDATE'), "
            "has_table_privilege(current_user, 'task_results', 'DELETE')"
        ).fetchone()
        assert privileges == (False, False)
        assert connection.execute(
            "SELECT result FROM task_results WHERE tenant_id = %s AND task_id = %s",
            (tenant_id, task_id),
        ).fetchone() == ({"ok": True},)


def test_unregistered_task_type_fails_closed() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant_id = uuid4()
    _, run_id = store.create_case_and_run(tenant_id, "allowlist")
    task_id = store.enqueue(tenant_id, run_id, "unknown", "unknown-task", {})
    worker = Worker(store, tenant_id, "allowlisted-worker", {})
    assert worker.run_once() == "failed"
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        assert connection.execute(
            "SELECT status, last_error FROM tasks WHERE id = %s", (task_id,)
        ).fetchone() == ("failed", "unregistered_task_type")


def test_contextual_worker_handler_receives_fenced_task_identity() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant_id = uuid4()
    _, run_id = store.create_case_and_run(tenant_id, "contextual handler")
    task_id = store.enqueue(tenant_id, run_id, "contextual", "context-task", {"value": 7})
    received: list[tuple[UUID, UUID, int, dict[str, object]]] = []

    def process(
        payload: dict[str, object], task: LeasedTask, context: WorkerTaskContext
    ) -> dict[str, object]:
        assert not context.cancelled.is_set()
        received.append((task.id, task.run_id, task.fencing_token, payload))
        return {"done": True}

    worker = Worker(
        store,
        tenant_id,
        "context-worker",
        {},
        contextual_handlers={"contextual": process},
    )
    assert worker.run_once() == "completed"
    assert received == [(task_id, run_id, 1, {"value": 7})]


def test_task_cancellation_reaches_context_handler_and_discards_result() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant = uuid4()
    _, run_id = store.create_case_and_run(tenant, "cooperative cancellation")
    task_id = store.enqueue(tenant, run_id, "contextual", "cancel-context", {})
    handler_started = Event()
    cancellation_observed = Event()
    outcomes: list[str] = []

    def wait_for_cancellation(
        _payload: dict[str, object], _task: LeasedTask, context: WorkerTaskContext
    ) -> dict[str, object]:
        handler_started.set()
        if context.cancelled.wait(5):
            cancellation_observed.set()
        return {"must_not_commit": True}

    worker = Worker(
        store,
        tenant,
        "cancel-context-worker",
        {},
        contextual_handlers={"contextual": wait_for_cancellation},
        lease_seconds=1,
        heartbeat_interval_seconds=0.02,
    )
    thread = Thread(target=lambda: outcomes.append(worker.run_once()), daemon=True)
    thread.start()
    try:
        assert handler_started.wait(5)
        assert store.cancel(tenant, task_id)
        thread.join(5)
        assert not thread.is_alive()
    finally:
        if thread.is_alive():
            store.cancel(tenant, task_id)
            thread.join(5)

    assert cancellation_observed.is_set()
    assert outcomes == ["lease_lost"]
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status FROM tasks WHERE id = %s", (task_id,)
        ).fetchone() == ("cancelled",)
        assert connection.execute(
            "SELECT count(*) FROM task_results WHERE task_id = %s", (task_id,)
        ).fetchone() == (0,)


def test_long_handler_renews_lease_until_completion() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant = uuid4()
    _, run_id = store.create_case_and_run(tenant, "long handler")
    task_id = store.enqueue(tenant, run_id, "slow", "long-handler", {})

    def slow(_payload: dict[str, object]) -> dict[str, object]:
        time.sleep(1.5)
        return {"ok": True}

    worker = Worker(store, tenant, "slow-worker", {"slow": slow}, lease_seconds=1)
    assert worker.run_once() == "completed"
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status, attempt FROM tasks WHERE id = %s", (task_id,)
        ).fetchone() == ("completed", 1)


def test_failed_heartbeat_prevents_result_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant = uuid4()
    _, run_id = store.create_case_and_run(tenant, "lost heartbeat")
    task_id = store.enqueue(tenant, run_id, "slow", "lost-heartbeat", {})

    def broken_heartbeat(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(store, "heartbeat", broken_heartbeat)

    def slow(_payload: dict[str, object]) -> dict[str, object]:
        time.sleep(0.25)
        return {"ok": True}

    worker = Worker(
        store,
        tenant,
        "lost-worker",
        {"slow": slow},
        lease_seconds=1,
        heartbeat_interval_seconds=0.05,
    )
    assert worker.run_once() == "lease_lost"
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status FROM tasks WHERE id = %s", (task_id,)
        ).fetchone() == ("running",)
        assert connection.execute(
            "SELECT count(*) FROM task_results WHERE task_id = %s", (task_id,)
        ).fetchone() == (0,)
