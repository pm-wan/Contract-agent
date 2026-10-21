"""Application-owned worker runner; AgentScope is never the durable task owner."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Event, Thread
from time import perf_counter
from typing import Any, Literal
from uuid import UUID

from opentelemetry import trace
from opentelemetry.trace import Span, Tracer

from contract_app.telemetry import context_from_traceparent, current_trace_id, emit_operation
from contract_app.workflow.cancellation import CancellationSignal
from contract_app.workflow.job_store import JobStore, LeasedTask

TaskHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class WorkerTaskContext:
    """Cooperative signals available to a handler for its current fenced lease."""

    cancelled: CancellationSignal


ContextualTaskHandler = Callable[[dict[str, Any], LeasedTask, WorkerTaskContext], dict[str, Any]]
WorkerOutcome = Literal["idle", "completed", "retried", "failed", "waiting_human", "lease_lost"]


class FactReviewRequired(Exception):
    """Handler requests a durable fact-review wait, not a professional conclusion."""

    def __init__(self, reason_code: str, required_role: str) -> None:
        if (
            re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason_code) is None
            or not required_role
            or required_role != required_role.strip()
            or len(required_role) > 64
        ):
            raise ValueError("invalid_fact_review_request")
        self.reason_code = reason_code
        self.required_role = required_role
        super().__init__(reason_code)


class Worker:
    def __init__(
        self,
        store: JobStore,
        tenant_id: UUID,
        worker_id: str,
        handlers: Mapping[str, TaskHandler],
        *,
        contextual_handlers: Mapping[str, ContextualTaskHandler] | None = None,
        lease_seconds: int = 60,
        max_attempts: int = 3,
        retry_delay_seconds: int = 5,
        heartbeat_interval_seconds: float | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        interval = (
            min(20.0, lease_seconds / 3)
            if heartbeat_interval_seconds is None
            else heartbeat_interval_seconds
        )
        if (
            not worker_id
            or lease_seconds <= 0
            or max_attempts <= 0
            or retry_delay_seconds < 0
            or interval <= 0
            or interval >= lease_seconds
        ):
            raise ValueError("invalid_worker_configuration")
        self.store = store
        self.tenant_id = tenant_id
        self.worker_id = worker_id
        self.handlers = dict(handlers)
        self.contextual_handlers = dict(contextual_handlers or {})
        if self.handlers.keys() & self.contextual_handlers.keys():
            raise ValueError("duplicate_worker_handler")
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.heartbeat_interval_seconds = interval
        self.tracer = tracer or trace.get_tracer("contract_app.workflow.worker")

    def run_once(self) -> WorkerOutcome:
        started = perf_counter()
        task = self.store.claim(self.tenant_id, self.worker_id, self.lease_seconds)
        parent_context = context_from_traceparent(task.traceparent if task is not None else None)
        with self.tracer.start_as_current_span(
            "workflow.task",
            context=parent_context,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("tenant.id", str(self.tenant_id))
            try:
                outcome = self._run_once(task, span)
            except Exception:
                span.set_attribute("workflow.outcome", "error")
                self._log_outcome(
                    "error", started, task.queue_wait_ms if task is not None else None
                )
                raise
            span.set_attribute("workflow.outcome", outcome)
            self._log_outcome(outcome, started, task.queue_wait_ms if task is not None else None)
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
        emit_operation("workflow.task", **fields)

    def _run_once(self, task: LeasedTask | None, span: Span) -> WorkerOutcome:
        if task is None:
            return "idle"
        span.set_attribute("run.id", str(task.run_id))
        span.set_attribute("task.id", str(task.id))
        span.set_attribute(
            "task.type",
            task.task_type
            if task.task_type in self.handlers or task.task_type in self.contextual_handlers
            else "<unregistered>",
        )
        span.set_attribute("task.attempt", task.attempt)
        handler = self.handlers.get(task.task_type)
        contextual_handler = self.contextual_handlers.get(task.task_type)
        if handler is None and contextual_handler is None:
            failed = self.store.fail(self.tenant_id, task, self.worker_id, "unregistered_task_type")
            return "failed" if failed else "lease_lost"
        stop_heartbeat = Event()
        lost_lease = Event()

        def pulse() -> None:
            while not stop_heartbeat.wait(self.heartbeat_interval_seconds):
                try:
                    renewed = self.store.heartbeat(
                        self.tenant_id, task, self.worker_id, self.lease_seconds
                    )
                except Exception:
                    # A failed renewal is not proof that this worker still owns the lease.
                    renewed = False
                if not renewed:
                    lost_lease.set()
                    return

        heartbeat_thread = Thread(target=pulse, name="task-lease-heartbeat", daemon=True)
        heartbeat_thread.start()
        error: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            if contextual_handler is not None:
                result = contextual_handler(task.payload, task, WorkerTaskContext(lost_lease))
            else:
                assert handler is not None
                result = handler(task.payload)
            if not isinstance(result, dict):
                raise TypeError("handler_result_must_be_dict")
        except Exception as exc:
            error = exc
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join()
        if lost_lease.is_set():
            return "lease_lost"
        if isinstance(error, FactReviewRequired):
            human_task_id = self.store.wait_for_fact_review(
                self.tenant_id,
                task,
                self.worker_id,
                error.reason_code,
                error.required_role,
            )
            return "waiting_human" if human_task_id is not None else "lease_lost"
        if error is not None:
            # Persist only the exception type: arbitrary exception text may contain contract data.
            safe_error_code = getattr(error, "safe_error_code", None)
            error_code = (
                safe_error_code
                if isinstance(safe_error_code, str)
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", safe_error_code)
                else type(error).__name__
            )
            if task.attempt >= self.max_attempts:
                failed = self.store.fail(self.tenant_id, task, self.worker_id, error_code)
                return "failed" if failed else "lease_lost"
            retried = self.store.retry(
                self.tenant_id, task, self.worker_id, error_code, self.retry_delay_seconds
            )
            return "retried" if retried else "lease_lost"
        assert result is not None
        completed = self.store.complete(self.tenant_id, task, self.worker_id, result)
        return "completed" if completed else "lease_lost"
