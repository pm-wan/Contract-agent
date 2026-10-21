import os
from uuid import UUID, uuid4

import psycopg
import pytest

from contract_app.workflow.job_store import HumanTaskConflict, JobStore
from contract_app.workflow.worker import FactReviewRequired, Worker

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_ADMIN_DATABASE_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_ADMIN_DATABASE_URL,
    reason="both TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL are required",
)


def _member(tenant_id: UUID, case_id: UUID, actor_id: UUID, role: str) -> None:
    assert TEST_ADMIN_DATABASE_URL is not None
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        connection.execute(
            "INSERT INTO case_members (tenant_id, case_id, actor_id, role) VALUES (%s, %s, %s, %s)",
            (tenant_id, case_id, actor_id, role),
        )


def test_fact_review_wait_survives_restart_and_resumes_only_for_member() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, outsider_tenant = uuid4(), uuid4()
    reviewer, wrong_role, stranger = uuid4(), uuid4(), uuid4()
    store = JobStore(TEST_DATABASE_URL)
    case_id, run_id = store.create_case_and_run(tenant, "fact review")
    _member(tenant, case_id, reviewer, "reviewer")
    _member(tenant, case_id, wrong_role, "submitter")
    task_id = store.enqueue(tenant, run_id, "check", "review", {})
    leased = store.claim(tenant, "worker-before-wait")
    assert leased is not None
    human_task_id = store.wait_for_fact_review(
        tenant, leased, "worker-before-wait", "fact_conflict", "reviewer"
    )
    assert human_task_id is not None
    assert not store.complete(tenant, leased, "worker-before-wait", {"ok": True})
    assert not store.heartbeat(tenant, leased, "worker-before-wait", 60)

    restarted = JobStore(TEST_DATABASE_URL)
    assert restarted.claim(tenant, "worker-after-restart") is None
    assert (
        restarted.resolve_fact_review(
            outsider_tenant, human_task_id, reviewer, 1, "yes", "key-tenant-0001"
        )
        is None
    )
    assert (
        restarted.resolve_fact_review(tenant, human_task_id, stranger, 1, "yes", "key-stranger-01")
        is None
    )
    assert (
        restarted.resolve_fact_review(tenant, human_task_id, wrong_role, 1, "yes", "key-role-00001")
        is None
    )
    with pytest.raises(HumanTaskConflict):
        restarted.resolve_fact_review(tenant, human_task_id, reviewer, 2, "yes", "key-stale-0001")
    assert restarted.resolve_fact_review(
        tenant, human_task_id, reviewer, 1, "verified source", "key-confirm-001"
    )
    assert restarted.resolve_fact_review(
        tenant, human_task_id, reviewer, 1, "verified source", "key-confirm-001"
    )
    with pytest.raises(HumanTaskConflict):
        restarted.resolve_fact_review(
            tenant, human_task_id, reviewer, 1, "duplicate", "key-other-00001"
        )
    resumed = restarted.claim(tenant, "worker-after-restart")
    assert resumed is not None and resumed.id == task_id
    assert resumed.attempt == 2 and resumed.fencing_token == 3
    assert restarted.complete(tenant, resumed, "worker-after-restart", {"reviewed": True})

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        row = connection.execute(
            "SELECT status, version, decision, decided_by, decision_reason "
            "FROM human_tasks WHERE id = %s",
            (human_task_id,),
        ).fetchone()
        assert row == ("completed", 2, "confirm_fact", reviewer, "verified source")
        events = connection.execute(
            "SELECT event_type FROM outbox_events WHERE task_id = %s ORDER BY created_at",
            (task_id,),
        ).fetchall()
        assert [item[0] for item in events] == [
            "human_task.created",
            "human_task.completed",
            "task.completed",
        ]


@pytest.mark.parametrize("end_state", ["cancelled", "superseded"])
def test_terminating_run_closes_open_human_task(end_state: str) -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant, reviewer = uuid4(), uuid4()
    case_id, run_id = store.create_case_and_run(tenant, "terminate human wait")
    _member(tenant, case_id, reviewer, "reviewer")
    task_id = store.enqueue(tenant, run_id, "check", "terminate-wait", {})
    task = store.claim(tenant, "worker")
    assert task is not None
    human_task_id = store.wait_for_fact_review(tenant, task, "worker", "fact_conflict", "reviewer")
    assert human_task_id is not None
    end_run = store.cancel_run if end_state == "cancelled" else store.supersede_run
    assert end_run(tenant, run_id)
    assert (
        store.resolve_fact_review(tenant, human_task_id, reviewer, 1, "too late", "key-late-000001")
        is None
    )
    assert store.claim(tenant, "next-worker") is None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status FROM tasks WHERE id = %s", (task_id,)
        ).fetchone() == (end_state,)
        assert connection.execute(
            "SELECT status, decision FROM human_tasks WHERE id = %s", (human_task_id,)
        ).fetchone() == (end_state, None)


def test_one_task_can_wait_again_without_overwriting_prior_decision() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant, reviewer = uuid4(), uuid4()
    case_id, run_id = store.create_case_and_run(tenant, "repeat review")
    _member(tenant, case_id, reviewer, "reviewer")
    task_id = store.enqueue(tenant, run_id, "check", "review-again", {})
    first_lease = store.claim(tenant, "worker-1")
    assert first_lease is not None
    first_human = store.wait_for_fact_review(
        tenant, first_lease, "worker-1", "fact_conflict", "reviewer"
    )
    assert first_human is not None
    assert store.resolve_fact_review(
        tenant, first_human, reviewer, 1, "first source", "key-first-00001"
    )
    second_lease = store.claim(tenant, "worker-2")
    assert second_lease is not None
    second_human = store.wait_for_fact_review(
        tenant, second_lease, "worker-2", "new_conflict", "reviewer"
    )
    assert second_human is not None and second_human != first_human
    assert store.claim(tenant, "worker-3") is None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT count(*) FROM human_tasks WHERE task_id = %s", (task_id,)
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM outbox_events WHERE task_id = %s "
            "AND event_type = 'human_task.created'",
            (task_id,),
        ).fetchone() == (2,)


def test_worker_enters_wait_and_resumes_after_persisted_decision() -> None:
    assert TEST_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant, reviewer = uuid4(), uuid4()
    case_id, run_id = store.create_case_and_run(tenant, "worker human wait")
    _member(tenant, case_id, reviewer, "reviewer")
    task_id = store.enqueue(tenant, run_id, "fact_check", "worker-wait", {})

    def check_after_review(_payload: dict[str, object]) -> dict[str, object]:
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
            row = connection.execute(
                "SELECT count(*) FROM human_tasks WHERE task_id = %s "
                "AND status = 'completed' AND decision = 'confirm_fact'",
                (task_id,),
            ).fetchone()
            assert row is not None
            if row[0] == 0:
                raise FactReviewRequired("fact_conflict", "reviewer")
        return {"reviewed": True}

    worker = Worker(store, tenant, "before-restart", {"fact_check": check_after_review})
    assert worker.run_once() == "waiting_human"
    assert worker.run_once() == "idle"
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status FROM audit_runs WHERE id = %s", (run_id,)
        ).fetchone() == ("waiting",)
        row = connection.execute(
            "SELECT id FROM human_tasks WHERE task_id = %s AND status = 'open'", (task_id,)
        ).fetchone()
        assert row is not None
        human_task_id = row[0]
    assert store.resolve_fact_review(
        tenant, human_task_id, reviewer, 1, "source checked", "key-source-0001"
    )
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status FROM audit_runs WHERE id = %s", (run_id,)
        ).fetchone() == ("queued",)
    restarted = Worker(
        JobStore(TEST_DATABASE_URL), tenant, "after-restart", {"fact_check": check_after_review}
    )
    assert restarted.run_once() == "completed"
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status, attempt FROM tasks WHERE id = %s", (task_id,)
        ).fetchone() == ("completed", 2)
        assert connection.execute(
            "SELECT status FROM audit_runs WHERE id = %s", (run_id,)
        ).fetchone() == ("completed",)


def test_revoked_member_cannot_decide_and_task_cancel_closes_wait() -> None:
    assert TEST_DATABASE_URL is not None
    assert TEST_ADMIN_DATABASE_URL is not None
    store = JobStore(TEST_DATABASE_URL)
    tenant, reviewer = uuid4(), uuid4()
    case_id, run_id = store.create_case_and_run(tenant, "revoked review")
    _member(tenant, case_id, reviewer, "reviewer")
    task_id = store.enqueue(tenant, run_id, "check", "revoke-member", {})
    task = store.claim(tenant, "worker")
    assert task is not None
    human_task_id = store.wait_for_fact_review(tenant, task, "worker", "fact_conflict", "reviewer")
    assert human_task_id is not None
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE case_members SET revoked_at = now() "
            "WHERE tenant_id = %s AND case_id = %s AND actor_id = %s",
            (tenant, case_id, reviewer),
        )
    assert (
        store.resolve_fact_review(tenant, human_task_id, reviewer, 1, "too late", "key-late-000002")
        is None
    )
    assert store.cancel(tenant, task_id)
    assert (
        store.resolve_fact_review(
            tenant, human_task_id, reviewer, 1, "cancelled", "key-cancel-0001"
        )
        is None
    )
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status FROM human_tasks WHERE id = %s", (human_task_id,)
        ).fetchone() == ("cancelled",)
