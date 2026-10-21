"""Tenant-scoped, at-least-once outbox delivery with fenced leases."""

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal
from uuid import UUID

import psycopg
from opentelemetry import trace
from opentelemetry.trace import Span, Tracer
from psycopg.rows import dict_row

from contract_app.telemetry import context_from_traceparent, current_trace_id, emit_operation


@dataclass(frozen=True, slots=True)
class LeasedEvent:
    tenant_id: UUID
    id: UUID
    task_id: UUID
    run_id: UUID
    event_type: str
    payload: dict[str, Any]
    attempt: int
    fencing_token: int
    traceparent: str | None
    queue_wait_ms: float = 0.0


class OutboxStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self, tenant_id: UUID) -> psycopg.Connection[Any]:
        connection: psycopg.Connection[Any] = psycopg.connect(
            self._database_url, row_factory=dict_row
        )
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return connection

    def claim(
        self, tenant_id: UUID, dispatcher_id: str, lease_seconds: int = 60
    ) -> LeasedEvent | None:
        if not dispatcher_id or lease_seconds <= 0:
            raise ValueError("invalid_delivery_lease")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                WITH candidate AS (
                  SELECT id,
                    (EXTRACT(EPOCH FROM (clock_timestamp() -
                      GREATEST(next_attempt_at, COALESCE(delivery_until, created_at)))) * 1000)
                      ::double precision AS queue_wait_ms
                  FROM outbox_events
                  WHERE published_at IS NULL AND dead_lettered_at IS NULL
                    AND next_attempt_at <= now()
                    AND (delivery_until IS NULL OR delivery_until < now())
                  ORDER BY next_attempt_at, created_at, id
                  FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE outbox_events AS e
                SET delivery_owner = %s,
                    delivery_until = now() + make_interval(secs => %s),
                    delivery_fencing_token = e.delivery_fencing_token + 1,
                    attempt = e.attempt + 1
                FROM candidate
                WHERE e.id = candidate.id AND e.tenant_id = %s
                RETURNING e.id, e.task_id, e.run_id, e.event_type, e.payload,
                          e.attempt, e.delivery_fencing_token, e.traceparent,
                          candidate.queue_wait_ms
                """,
                (dispatcher_id, lease_seconds, tenant_id),
            ).fetchone()
            if row is None:
                return None
            return LeasedEvent(
                tenant_id=tenant_id,
                id=row["id"],
                task_id=row["task_id"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                payload=row["payload"],
                attempt=row["attempt"],
                fencing_token=row["delivery_fencing_token"],
                traceparent=row["traceparent"],
                queue_wait_ms=row["queue_wait_ms"],
            )

    def mark_published(self, event: LeasedEvent, dispatcher_id: str) -> bool:
        with self._connect(event.tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE outbox_events SET published_at = now(),
                    delivery_owner = NULL, delivery_until = NULL
                WHERE tenant_id = %s AND id = %s AND published_at IS NULL
                  AND dead_lettered_at IS NULL AND delivery_owner = %s
                  AND delivery_fencing_token = %s AND delivery_until > now()
                RETURNING id
                """,
                (event.tenant_id, event.id, dispatcher_id, event.fencing_token),
            ).fetchone()
            return row is not None

    def delivery_failed(
        self,
        event: LeasedEvent,
        dispatcher_id: str,
        error_code: str,
        *,
        retry_delay_seconds: int,
        max_attempts: int,
    ) -> bool:
        if not error_code or len(error_code) > 128 or retry_delay_seconds < 0 or max_attempts <= 0:
            raise ValueError("invalid_delivery_failure")
        with self._connect(event.tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE outbox_events
                SET delivery_owner = NULL, delivery_until = NULL,
                    next_attempt_at = now() + make_interval(secs => %s),
                    last_error = %s,
                    dead_lettered_at = CASE WHEN attempt >= %s THEN now()
                                            ELSE dead_lettered_at END
                WHERE tenant_id = %s AND id = %s AND published_at IS NULL
                  AND dead_lettered_at IS NULL AND delivery_owner = %s
                  AND delivery_fencing_token = %s AND delivery_until > now()
                RETURNING id
                """,
                (
                    retry_delay_seconds,
                    error_code,
                    max_attempts,
                    event.tenant_id,
                    event.id,
                    dispatcher_id,
                    event.fencing_token,
                ),
            ).fetchone()
            return row is not None


DeliveryOutcome = Literal["idle", "published", "retried", "dead_lettered", "lease_lost"]
Publisher = Callable[[LeasedEvent], None]


class OutboxDispatcher:
    """Publisher returns only after acknowledgement; consumers dedupe by (tenant_id, event_id)."""

    def __init__(
        self,
        store: OutboxStore,
        tenant_id: UUID,
        dispatcher_id: str,
        publish: Publisher,
        *,
        lease_seconds: int = 60,
        retry_delay_seconds: int = 5,
        max_attempts: int = 3,
        tracer: Tracer | None = None,
    ) -> None:
        if not dispatcher_id or lease_seconds <= 0 or retry_delay_seconds < 0 or max_attempts <= 0:
            raise ValueError("invalid_dispatcher_configuration")
        self.store = store
        self.tenant_id = tenant_id
        self.dispatcher_id = dispatcher_id
        self.publish = publish
        self.lease_seconds = lease_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self.max_attempts = max_attempts
        self.tracer = tracer or trace.get_tracer("contract_app.workflow.outbox")

    def dispatch_once(self) -> DeliveryOutcome:
        started = perf_counter()
        event = self.store.claim(self.tenant_id, self.dispatcher_id, self.lease_seconds)
        parent_context = context_from_traceparent(event.traceparent if event is not None else None)
        with self.tracer.start_as_current_span(
            "outbox.delivery",
            context=parent_context,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("tenant.id", str(self.tenant_id))
            try:
                outcome = self._dispatch_once(event, span)
            except Exception:
                span.set_attribute("outbox.outcome", "error")
                self._log_outcome(
                    "error", started, event.queue_wait_ms if event is not None else None
                )
                raise
            span.set_attribute("outbox.outcome", outcome)
            self._log_outcome(outcome, started, event.queue_wait_ms if event is not None else None)
            return outcome

    def _log_outcome(
        self, outcome: str, started: float, queue_wait_ms: float | None = None
    ) -> None:
        fields: dict[str, str | int | float] = {
            "tenant_id": str(self.tenant_id),
            "outcome": outcome,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
        }
        if queue_wait_ms is not None:
            fields["queue_wait_ms"] = queue_wait_ms
        trace_id = current_trace_id()
        if trace_id is not None:
            fields["trace_id"] = trace_id
        emit_operation("outbox.delivery", **fields)

    def _dispatch_once(self, event: LeasedEvent | None, span: Span) -> DeliveryOutcome:
        if event is None:
            return "idle"
        span.set_attribute("run.id", str(event.run_id))
        span.set_attribute("task.id", str(event.task_id))
        span.set_attribute("event.id", str(event.id))
        event_type = (
            event.event_type
            if event.event_type
            in {"task.completed", "task.failed", "human_task.created", "human_task.completed"}
            else "<unknown>"
        )
        span.set_attribute("event.type", event_type)
        span.set_attribute("event.attempt", event.attempt)
        try:
            self.publish(event)
        except Exception as exc:
            # Never persist arbitrary exception text: it may include sensitive material.
            failed = self.store.delivery_failed(
                event,
                self.dispatcher_id,
                type(exc).__name__,
                retry_delay_seconds=self.retry_delay_seconds,
                max_attempts=self.max_attempts,
            )
            if not failed:
                return "lease_lost"
            return "dead_lettered" if event.attempt >= self.max_attempts else "retried"
        published = self.store.mark_published(event, self.dispatcher_id)
        return "published" if published else "lease_lost"
