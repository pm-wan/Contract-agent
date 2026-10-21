import os
from uuid import UUID, uuid4

import psycopg
import pytest

from contract_app.workflow.job_store import JobStore
from contract_app.workflow.outbox import LeasedEvent, OutboxDispatcher, OutboxStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


def _completed_task(tenant_id: UUID) -> None:
    assert TEST_DATABASE_URL is not None
    jobs = JobStore(TEST_DATABASE_URL)
    _, run_id = jobs.create_case_and_run(tenant_id, "outbox fixture")
    jobs.enqueue(tenant_id, run_id, "fixture", str(uuid4()), {})
    task = jobs.claim(tenant_id, "task-worker")
    assert task is not None
    assert jobs.complete(tenant_id, task, "task-worker", {"ok": True})


def _expire_lease(tenant_id: UUID, event_id: UUID) -> None:
    assert TEST_DATABASE_URL is not None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        connection.execute(
            "UPDATE outbox_events SET delivery_until = now() - interval '1 second' WHERE id = %s",
            (event_id,),
        )


def test_claim_recovery_fencing_and_tenant_isolation() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, other_tenant = uuid4(), uuid4()
    _completed_task(tenant)
    store = OutboxStore(TEST_DATABASE_URL)
    assert store.claim(other_tenant, "outsider") is None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE outbox_events SET created_at = now() - interval '2 seconds', "
            "next_attempt_at = now() - interval '2 seconds'"
        )
    first = store.claim(tenant, "dispatcher-1")
    assert first is not None and first.attempt == 1 and first.fencing_token == 1
    assert first.queue_wait_ms >= 1500
    assert store.claim(tenant, "dispatcher-2") is None
    _expire_lease(tenant, first.id)
    second = store.claim(tenant, "dispatcher-2")
    assert second is not None and second.id == first.id
    assert second.attempt == 2 and second.fencing_token == 2
    assert second.queue_wait_ms >= 500
    assert not store.mark_published(first, "dispatcher-1")
    assert store.mark_published(second, "dispatcher-2")
    assert not store.mark_published(second, "dispatcher-2")
    assert store.claim(tenant, "dispatcher-3") is None


def test_dispatcher_retries_then_dead_letters_without_leaking_error_text() -> None:
    assert TEST_DATABASE_URL is not None
    tenant = uuid4()
    _completed_task(tenant)
    store = OutboxStore(TEST_DATABASE_URL)

    def failing_publish(_event: LeasedEvent) -> None:
        raise RuntimeError("sensitive contract contents")

    dispatcher = OutboxDispatcher(
        store, tenant, "dispatcher", failing_publish, retry_delay_seconds=0, max_attempts=2
    )
    assert dispatcher.dispatch_once() == "retried"
    assert dispatcher.dispatch_once() == "dead_lettered"
    assert dispatcher.dispatch_once() == "idle"
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        row = connection.execute(
            "SELECT attempt, last_error, dead_lettered_at, published_at FROM outbox_events"
        ).fetchone()
        assert row is not None
        assert row[0] == 2 and row[1] == "RuntimeError"
        assert row[2] is not None and row[3] is None


def test_publish_ack_gap_redelivers_same_id_for_consumer_dedupe() -> None:
    assert TEST_DATABASE_URL is not None
    tenant = uuid4()
    _completed_task(tenant)
    store = OutboxStore(TEST_DATABASE_URL)
    first = store.claim(tenant, "crashed-dispatcher")
    assert first is not None
    deliveries = [first.id]  # Broker accepted; process died before database acknowledgement.
    _expire_lease(tenant, first.id)
    seen: set[tuple[UUID, UUID]] = set()
    accepted: list[UUID] = []

    def deduplicating_consumer(event: LeasedEvent) -> None:
        deliveries.append(event.id)
        key = (event.tenant_id, event.id)
        if key not in seen:
            seen.add(key)
            accepted.append(event.id)

    dispatcher = OutboxDispatcher(store, tenant, "replacement", deduplicating_consumer)
    assert dispatcher.dispatch_once() == "published"
    assert deliveries == [first.id, first.id]
    assert accepted == [first.id]
