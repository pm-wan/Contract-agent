"""Tenant-scoped durable task leasing with fencing and atomic outbox writes."""

import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from contract_app.retrieval.authorization import (
    AuthorizationProjector,
    TaskAccess,
)
from contract_app.retrieval.pageindex_adapter import RetrievalDenied
from contract_app.rules.obligations import OBLIGATION_CATEGORY_ORDER
from contract_app.telemetry import capture_traceparent
from contract_app.workflow.execution_trace import (
    ZERO_HASH,
    ExecutionTraceEvent,
    ExecutionTraceSnapshot,
    RunModelUsageSummary,
)

MAX_UPSTREAM_COMPARISON_DOCUMENTS = 8


@dataclass(frozen=True, slots=True)
class LeasedTask:
    id: UUID
    run_id: UUID
    task_type: str
    payload: dict[str, Any]
    attempt: int
    fencing_token: int
    lease_owner: str
    traceparent: str | None
    queue_wait_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class RunView:
    id: UUID
    case_id: UUID
    input_snapshot_id: UUID | None
    status: str
    version: int
    created_at: datetime
    task_type: str | None = None


@dataclass(frozen=True, slots=True)
class RunResultsView:
    run: RunView
    results: tuple[dict[str, Any], ...]
    reported_model_usage: RunModelUsageSummary | None = None
    can_confirm_facts: bool = False


@dataclass(frozen=True, slots=True)
class UpstreamComparisonView:
    run: RunView
    result: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class HumanTaskView:
    id: UUID
    case_id: UUID
    run_id: UUID
    type: str
    status: str
    reason_code: str
    required_role: str
    version: int
    decision: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FactSetSnapshotView:
    id: UUID
    case_id: UUID
    input_snapshot_id: UUID
    source_run_id: UUID
    version: int
    facts: dict[str, Any]
    content_hash: str
    created_by: UUID
    reason: str
    created_at: datetime


def _summarize_run_model_usage(events: list[dict[str, Any]]) -> RunModelUsageSummary | None:
    if not events:
        return None
    requests = responses = reports = 0
    input_tokens = output_tokens = 0
    last_sequence: dict[tuple[UUID, int], int] = {}
    last_event_sha256: dict[tuple[UUID, int], str] = {}
    in_flight: set[tuple[UUID, int]] = set()
    trace_order_valid = True
    for event in events:
        attempt_key = (event["task_id"], event["attempt"])
        expected_sequence = last_sequence.get(attempt_key, 0) + 1
        previous = last_event_sha256.get(attempt_key, ZERO_HASH)
        if event["sequence"] != expected_sequence or event["previous_event_sha256"] != previous:
            trace_order_valid = False
        trace_event = ExecutionTraceEvent.model_validate(
            {
                "sequence": event["sequence"],
                "event_type": event["event_type"],
                "payload_sha256": event["payload_sha256"],
                "payload_bytes": event["payload_bytes"],
                "previous_event_sha256": event["previous_event_sha256"],
                "event_sha256": event["event_sha256"],
                "input_tokens": event["input_tokens"],
                "output_tokens": event["output_tokens"],
            }
        )
        expected_digest = ExecutionTraceEvent.digest(
            trace_event.sequence,
            trace_event.event_type,
            trace_event.payload_sha256,
            trace_event.payload_bytes,
            trace_event.previous_event_sha256,
            trace_event.input_tokens,
            trace_event.output_tokens,
        )
        if trace_event.event_sha256 != expected_digest:
            raise ValueError("stored_execution_trace_event_digest_invalid")
        last_sequence[attempt_key] = trace_event.sequence
        last_event_sha256[attempt_key] = trace_event.event_sha256
        if trace_event.event_type == "model_request":
            requests += 1
            if attempt_key in in_flight:
                trace_order_valid = False
            in_flight.add(attempt_key)
        elif trace_event.event_type == "model_response":
            responses += 1
            if attempt_key not in in_flight:
                trace_order_valid = False
            else:
                in_flight.remove(attempt_key)
            if trace_event.input_tokens is not None and trace_event.output_tokens is not None:
                reports += 1
                input_tokens += trace_event.input_tokens
                output_tokens += trace_event.output_tokens
    if requests == 0 and responses == 0:
        return None
    return RunModelUsageSummary(
        model_requests=requests,
        model_responses=responses,
        usage_reports=reports,
        input_tokens=input_tokens if reports else None,
        output_tokens=output_tokens if reports else None,
        complete=(trace_order_valid and not in_flight and requests == responses == reports),
    )


def _serialize_task_result(result: dict[str, Any]) -> str:
    return json.dumps(
        result,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _task_result_content_hash(result: dict[str, Any]) -> str:
    return sha256(_serialize_task_result(result).encode("utf-8")).hexdigest()


def _fact_set_content_hash(facts: dict[str, Any]) -> str:
    serialized = json.dumps(
        facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _price_review_required(result: dict[str, Any]) -> bool:
    results = result.get("results")
    if not isinstance(results, list):
        return False
    for item in results:
        if not isinstance(item, dict) or item.get("rule_id") != "legal.price.definition":
            continue
        candidates = item.get("slot_candidates")
        if not isinstance(candidates, list):
            return True
        for candidate in candidates:
            if not isinstance(candidate, dict):
                return True
            if candidate.get("state") in {
                "needs_material",
                "conflicted",
                "human_review",
                "undetermined",
            }:
                return True
            if candidate.get("slot_id") == "payment_basis":
                values = candidate.get("value")
                if not isinstance(values, list) or any(
                    not isinstance(value, dict) or value.get("denominator") == "unspecified"
                    for value in values
                ):
                    return True
        limitations = item.get("limitations")
        if isinstance(limitations, list) and any(
            isinstance(limitation, str)
            and any(term in limitation for term in ("价款", "税额", "金额来源", "分母"))
            for limitation in limitations
        ):
            return True
    return False


def _validate_result_execution_trace(
    result: dict[str, Any], persisted_events: list[dict[str, Any]]
) -> None:
    """Bind a successful result's trace to its durable attempt ledger when present."""
    if not persisted_events:
        # Results written before the append-only ledger was introduced remain readable.
        return
    result_items = result.get("results")
    if not isinstance(result_items, list) or not result_items:
        raise ValueError("stored_execution_trace_missing")
    event_fields = (
        "sequence",
        "event_type",
        "payload_sha256",
        "payload_bytes",
        "previous_event_sha256",
        "input_tokens",
        "output_tokens",
        "event_sha256",
    )
    try:
        events = [
            ExecutionTraceEvent.model_validate({key: event[key] for key in event_fields})
            for event in persisted_events
        ]
        expected = ExecutionTraceSnapshot.from_events(events)
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("stored_execution_trace_invalid") from exc
    for result_item in result_items:
        if not isinstance(result_item, dict) or not isinstance(
            result_item.get("execution_trace"), dict
        ):
            raise ValueError("stored_execution_trace_missing")
        try:
            actual = ExecutionTraceSnapshot.model_validate(result_item["execution_trace"])
        except (TypeError, ValidationError, ValueError) as exc:
            raise ValueError("stored_execution_trace_invalid") from exc
        if actual != expected:
            raise ValueError("stored_execution_trace_ledger_mismatch")


class BoundRunDenied(PermissionError):
    """Actor cannot start a run for this case or input snapshot."""


class BoundRunConflict(ValueError):
    """The frozen input or idempotency key is no longer usable."""


class HumanTaskConflict(ValueError):
    """A human-task decision is stale or its idempotency key was reused."""


class JobStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self, tenant_id: UUID) -> psycopg.Connection[Any]:
        connection: psycopg.Connection[Any] = psycopg.connect(
            self._database_url, row_factory=dict_row
        )
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return connection

    def record_execution_trace_event(
        self, tenant_id: UUID, task: LeasedTask, event: ExecutionTraceEvent
    ) -> bool:
        """Append a content-free hash event only while this exact lease is current."""
        with self._connect(tenant_id) as connection:
            owned = connection.execute(
                """
                SELECT lease_owner FROM tasks
                WHERE tenant_id = %s AND id = %s AND run_id = %s
                  AND status = 'running' AND attempt = %s AND fencing_token = %s
                  AND lease_owner = %s AND lease_until > clock_timestamp()
                FOR UPDATE
                """,
                (
                    tenant_id,
                    task.id,
                    task.run_id,
                    task.attempt,
                    task.fencing_token,
                    task.lease_owner,
                ),
            ).fetchone()
            if owned is None:
                return False
            inserted = connection.execute(
                """
                INSERT INTO task_execution_trace_events (
                  tenant_id, task_id, run_id, attempt, sequence, event_type,
                  payload_sha256, payload_bytes, previous_event_sha256, event_sha256,
                  input_tokens, output_tokens
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, task_id, attempt, sequence) DO NOTHING
                RETURNING sequence
                """,
                (
                    tenant_id,
                    task.id,
                    task.run_id,
                    task.attempt,
                    event.sequence,
                    event.event_type,
                    event.payload_sha256,
                    event.payload_bytes,
                    event.previous_event_sha256,
                    event.event_sha256,
                    event.input_tokens,
                    event.output_tokens,
                ),
            ).fetchone()
            if inserted is not None:
                return True
            existing = connection.execute(
                """
                SELECT event_type, payload_sha256, payload_bytes, previous_event_sha256,
                       event_sha256, input_tokens, output_tokens
                FROM task_execution_trace_events
                WHERE tenant_id = %s AND task_id = %s AND attempt = %s AND sequence = %s
                """,
                (tenant_id, task.id, task.attempt, event.sequence),
            ).fetchone()
            expected = (
                event.event_type,
                event.payload_sha256,
                event.payload_bytes,
                event.previous_event_sha256,
                event.event_sha256,
                event.input_tokens,
                event.output_tokens,
            )
            if (
                existing is None
                or tuple(
                    existing[key]
                    for key in (
                        "event_type",
                        "payload_sha256",
                        "payload_bytes",
                        "previous_event_sha256",
                        "event_sha256",
                        "input_tokens",
                        "output_tokens",
                    )
                )
                != expected
            ):
                raise ValueError("execution_trace_event_conflict")
            return True

    def create_case_and_run(self, tenant_id: UUID, title: str) -> tuple[UUID, UUID]:
        """M0 fixture command; M1 will replace with versioned case/run creation."""
        if not title.strip():
            raise ValueError("case_title_required")
        with self._connect(tenant_id) as connection:
            case = connection.execute(
                "INSERT INTO audit_cases (tenant_id, title) VALUES (%s, %s) RETURNING id",
                (tenant_id, title),
            ).fetchone()
            assert case is not None
            run = connection.execute(
                "INSERT INTO audit_runs (tenant_id, case_id) VALUES (%s, %s) RETURNING id",
                (tenant_id, case["id"]),
            ).fetchone()
            assert run is not None
            return case["id"], run["id"]

    def create_bound_run(
        self,
        access: TaskAccess,
        request_key: str,
        *,
        initial_task_type: str | None = None,
        initial_task_payload: dict[str, Any] | None = None,
        definition_snapshot: dict[str, Any] | None = None,
    ) -> UUID:
        """Create a run only for a current, actor-authorized frozen input."""
        if access.input_snapshot_id is None:
            raise BoundRunConflict("input_snapshot_required")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", request_key) is None:
            raise ValueError("invalid_request_key")
        if initial_task_type is not None and not initial_task_type.strip():
            raise ValueError("task_type_required")
        if initial_task_type not in {None, "contract_review", "upstream_comparison"}:
            raise BoundRunDenied("run_task_type_not_enabled")
        if initial_task_payload is not None and initial_task_type is None:
            raise ValueError("task_type_required_for_payload")
        if initial_task_type == "contract_review" and definition_snapshot is None:
            raise ValueError("review_definition_snapshot_required")
        if initial_task_type == "contract_review" and (
            initial_task_payload is None
            or set(initial_task_payload).difference(
                {
                    "input_snapshot_id",
                    "fact_set_snapshot_id",
                    "dependency_result_references",
                }
            )
            or not {"input_snapshot_id"}.issubset(initial_task_payload)
            or initial_task_payload.get("input_snapshot_id") != str(access.input_snapshot_id)
            or (
                "dependency_result_references" in initial_task_payload
                and not isinstance(initial_task_payload["dependency_result_references"], list)
            )
        ):
            raise ValueError("review_task_payload_invalid")
        if initial_task_type == "upstream_comparison" and (
            initial_task_payload is None
            or set(initial_task_payload) != {"input_snapshot_id", "definition"}
            or initial_task_payload.get("input_snapshot_id") != str(access.input_snapshot_id)
            or not isinstance(initial_task_payload.get("definition"), dict)
        ):
            raise ValueError("upstream_comparison_task_payload_invalid")
        if definition_snapshot is not None and initial_task_type != "contract_review":
            raise ValueError("definition_snapshot_requires_contract_review_task")
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                connection.execute(
                    "SELECT set_config('app.tenant_id', %s, true)", (str(access.tenant_id),)
                )
                case = connection.execute(
                    "SELECT version, status FROM audit_cases WHERE tenant_id = %s AND id = %s "
                    "FOR UPDATE",
                    (access.tenant_id, access.case_id),
                ).fetchone()
                member = connection.execute(
                    "SELECT role FROM case_members WHERE tenant_id = %s AND case_id = %s "
                    "AND actor_id = %s AND revoked_at IS NULL",
                    (access.tenant_id, access.case_id, access.actor_id),
                ).fetchone()
                if (
                    case is None
                    or member is None
                    or member["role"]
                    not in {
                        "submitter",
                        "reviewer",
                    }
                ):
                    raise BoundRunDenied("case_member_not_authorized")
                snapshot = connection.execute(
                    "SELECT purpose, require_amount, case_version FROM input_snapshots "
                    "WHERE tenant_id = %s AND case_id = %s AND id = %s",
                    (access.tenant_id, access.case_id, access.input_snapshot_id),
                ).fetchone()
                if snapshot is None:
                    raise BoundRunDenied("input_snapshot_not_authorized")
                if (
                    case["status"] != "ready"
                    or case["version"] != snapshot["case_version"]
                    or snapshot["purpose"] != access.purpose
                    or snapshot["require_amount"] != access.task_requires_amount
                ):
                    raise BoundRunConflict("input_snapshot_stale_or_scope_changed")
                expected_purpose = {
                    "contract_review": "contract_review",
                    "upstream_comparison": "upstream_comparison",
                }.get(initial_task_type)
                if initial_task_type is None:
                    expected_purpose = "contract_review"
                if snapshot["purpose"] != expected_purpose:
                    raise BoundRunDenied("run_purpose_not_enabled")
                if initial_task_type == "upstream_comparison":
                    frozen_rows = connection.execute(
                        "SELECT relation FROM input_snapshot_documents "
                        "WHERE tenant_id = %s AND snapshot_id = %s",
                        (access.tenant_id, access.input_snapshot_id),
                    ).fetchall()
                    relations = [row["relation"] for row in frozen_rows]
                    if (
                        relations.count("main") != 1
                        or not 1
                        <= relations.count("upstream")
                        <= MAX_UPSTREAM_COMPARISON_DOCUMENTS
                    ):
                        raise BoundRunConflict("upstream_comparison_pair_incomplete")
                    if not access.task_requires_amount:
                        raise BoundRunDenied("upstream_comparison_amount_scope_required")
                bound_fact_set_id = (initial_task_payload or {}).get("fact_set_snapshot_id")
                if bound_fact_set_id is not None:
                    fact_set = connection.execute(
                        "SELECT 1 FROM fact_set_snapshots WHERE tenant_id = %s AND case_id = %s "
                        "AND input_snapshot_id = %s AND id = %s",
                        (
                            access.tenant_id,
                            access.case_id,
                            access.input_snapshot_id,
                            UUID(str(bound_fact_set_id)),
                        ),
                    ).fetchone()
                    if fact_set is None:
                        raise BoundRunConflict("fact_set_snapshot_binding_invalid")
                    if (initial_task_payload or {}).get("dependency_result_references"):
                        latest_fact_set = connection.execute(
                            "SELECT id FROM fact_set_snapshots WHERE tenant_id = %s "
                            "AND case_id = %s AND input_snapshot_id = %s "
                            "ORDER BY version DESC LIMIT 1",
                            (access.tenant_id, access.case_id, access.input_snapshot_id),
                        ).fetchone()
                        if latest_fact_set is None or str(latest_fact_set["id"]) != str(
                            bound_fact_set_id
                        ):
                            raise BoundRunConflict("dependency_result_reference_stale")
                frozen_rows = connection.execute(
                    "SELECT document_version_id FROM input_snapshot_documents "
                    "WHERE tenant_id = %s AND snapshot_id = %s",
                    (access.tenant_id, access.input_snapshot_id),
                ).fetchall()
                frozen_ids = {row["document_version_id"] for row in frozen_rows}
                projector = AuthorizationProjector(self._database_url)
                allowed = projector.indexed_documents_in_transaction(access, connection)
                if not frozen_ids or not frozen_ids.issubset(allowed):
                    raise BoundRunDenied("input_snapshot_sources_not_authorized")
                row = connection.execute(
                    "INSERT INTO audit_runs "
                    "(tenant_id, case_id, input_snapshot_id, started_by, request_key) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (tenant_id, request_key) WHERE request_key IS NOT NULL "
                    "DO NOTHING RETURNING id, case_id, input_snapshot_id, started_by",
                    (
                        access.tenant_id,
                        access.case_id,
                        access.input_snapshot_id,
                        access.actor_id,
                        request_key,
                    ),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        "SELECT id, case_id, input_snapshot_id, started_by FROM audit_runs "
                        "WHERE tenant_id = %s AND request_key = %s",
                        (access.tenant_id, request_key),
                    ).fetchone()
                if (
                    row is None
                    or row["case_id"] != access.case_id
                    or row["input_snapshot_id"] != access.input_snapshot_id
                    or row["started_by"] != access.actor_id
                ):
                    raise BoundRunConflict("request_key_conflict")
                if initial_task_type is not None:
                    payload = initial_task_payload or {}
                    task = connection.execute(
                        """
                        INSERT INTO tasks
                          (tenant_id, run_id, task_type, idempotency_key, payload, traceparent)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (tenant_id, task_type, idempotency_key) DO UPDATE
                          SET updated_at = tasks.updated_at
                        WHERE tasks.run_id = EXCLUDED.run_id AND tasks.payload = EXCLUDED.payload
                        RETURNING id
                        """,
                        (
                            access.tenant_id,
                            row["id"],
                            initial_task_type,
                            request_key,
                            json.dumps(payload),
                            capture_traceparent(),
                        ),
                    ).fetchone()
                    if task is None:
                        raise BoundRunConflict("initial_task_idempotency_conflict")
                    if definition_snapshot is not None:
                        connection.execute(
                            "INSERT INTO run_definition_snapshots (tenant_id, run_id, snapshot) "
                            "VALUES (%s, %s, %s::jsonb) ON CONFLICT (tenant_id, run_id) DO NOTHING",
                            (
                                access.tenant_id,
                                row["id"],
                                json.dumps(definition_snapshot, ensure_ascii=False),
                            ),
                        )
                return cast(UUID, row["id"])
        except psycopg.errors.SerializationFailure as exc:
            raise BoundRunConflict("concurrent_input_change") from exc

    def start_bound_run(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        input_snapshot_id: UUID,
        request_key: str,
        definition_snapshot: dict[str, Any],
        *,
        dependency_result_references: list[dict[str, Any]] | None = None,
    ) -> UUID:
        """Derive scope from the immutable snapshot and atomically queue its first task."""
        with self._connect(tenant_id) as connection:
            snapshot = connection.execute(
                "SELECT purpose, require_amount FROM input_snapshots "
                "WHERE tenant_id = %s AND case_id = %s AND id = %s",
                (tenant_id, case_id, input_snapshot_id),
            ).fetchone()
            existing_run = connection.execute(
                "SELECT run.id, run.case_id, run.input_snapshot_id, run.started_by, task.payload "
                "FROM audit_runs AS run LEFT JOIN tasks AS task "
                "ON task.tenant_id = run.tenant_id AND task.run_id = run.id "
                "AND task.task_type = 'contract_review' "
                "WHERE run.tenant_id = %s AND run.request_key = %s",
                (tenant_id, request_key),
            ).fetchone()
        if snapshot is None:
            raise BoundRunDenied("input_snapshot_not_authorized")
        if snapshot["purpose"] != "contract_review":
            raise BoundRunDenied("run_purpose_not_enabled")
        if existing_run is not None:
            if (
                existing_run["case_id"] != case_id
                or existing_run["input_snapshot_id"] != input_snapshot_id
                or existing_run["started_by"] != actor_id
                or existing_run["payload"] is None
                or existing_run["payload"].get("dependency_result_references", [])
                != (dependency_result_references or [])
            ):
                raise BoundRunConflict("request_key_conflict")
            initial_payload = existing_run["payload"]
        else:
            with self._connect(tenant_id) as connection:
                latest_fact_set = connection.execute(
                    "SELECT id FROM fact_set_snapshots WHERE tenant_id = %s AND case_id = %s "
                    "AND input_snapshot_id = %s ORDER BY version DESC LIMIT 1",
                    (tenant_id, case_id, input_snapshot_id),
                ).fetchone()
            initial_payload = {"input_snapshot_id": str(input_snapshot_id)}
            if latest_fact_set is not None:
                initial_payload["fact_set_snapshot_id"] = str(latest_fact_set["id"])
            if dependency_result_references:
                expected_fact_set_id = (
                    str(latest_fact_set["id"]) if latest_fact_set is not None else None
                )
                if any(
                    reference.get("case_id") != str(case_id)
                    or reference.get("input_snapshot_id") != str(input_snapshot_id)
                    or reference.get("fact_set_snapshot_id") != expected_fact_set_id
                    or reference.get("is_completion_proof") is not False
                    for reference in dependency_result_references
                ):
                    raise BoundRunConflict("dependency_result_reference_stale")
                initial_payload["dependency_result_references"] = dependency_result_references
        access = TaskAccess(
            tenant_id,
            case_id,
            actor_id,
            snapshot["purpose"],
            snapshot["require_amount"],
            input_snapshot_id,
        )
        return self.create_bound_run(
            access,
            request_key,
            initial_task_type="contract_review",
            initial_task_payload=initial_payload,
            definition_snapshot=definition_snapshot,
        )

    def start_upstream_comparison(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        input_snapshot_id: UUID,
        request_key: str,
        definition: dict[str, Any],
    ) -> UUID:
        """Queue a pairwise candidate comparison bound to a frozen upstream snapshot."""
        with self._connect(tenant_id) as connection:
            snapshot = connection.execute(
                "SELECT purpose, require_amount FROM input_snapshots "
                "WHERE tenant_id = %s AND case_id = %s AND id = %s",
                (tenant_id, case_id, input_snapshot_id),
            ).fetchone()
            existing_run = connection.execute(
                "SELECT run.id, run.case_id, run.input_snapshot_id, run.started_by, task.payload "
                "FROM audit_runs AS run JOIN tasks AS task "
                "ON task.tenant_id = run.tenant_id AND task.run_id = run.id "
                "AND task.task_type = 'upstream_comparison' "
                "WHERE run.tenant_id = %s AND run.request_key = %s",
                (tenant_id, request_key),
            ).fetchone()
        if snapshot is None or snapshot["purpose"] != "upstream_comparison":
            raise BoundRunDenied("upstream_comparison_snapshot_required")
        payload = {"input_snapshot_id": str(input_snapshot_id), "definition": definition}
        if existing_run is not None and (
            existing_run["case_id"] != case_id
            or existing_run["input_snapshot_id"] != input_snapshot_id
            or existing_run["started_by"] != actor_id
            or existing_run["payload"] != payload
        ):
            raise BoundRunConflict("request_key_conflict")
        access = TaskAccess(
            tenant_id,
            case_id,
            actor_id,
            "upstream_comparison",
            snapshot["require_amount"],
            input_snapshot_id,
        )
        return self.create_bound_run(
            access,
            request_key,
            initial_task_type="upstream_comparison",
            initial_task_payload=payload,
        )

    def get_run(self, tenant_id: UUID, actor_id: UUID, run_id: UUID) -> RunView:
        """Return only run metadata visible to a current member of its case."""
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT run.id, run.case_id, run.input_snapshot_id, run.status,
                       run.version, run.created_at,
                       (SELECT task.task_type FROM tasks AS task
                        WHERE task.tenant_id = run.tenant_id AND task.run_id = run.id
                        ORDER BY task.created_at LIMIT 1) AS task_type
                FROM audit_runs AS run
                JOIN case_members AS member
                  ON member.tenant_id = run.tenant_id AND member.case_id = run.case_id
                WHERE run.tenant_id = %s AND run.id = %s AND member.actor_id = %s
                  AND member.revoked_at IS NULL
                  AND member.role IN ('submitter', 'reviewer')
                """,
                (tenant_id, run_id, actor_id),
            ).fetchone()
        if row is None:
            raise BoundRunDenied("run_not_available")
        return RunView(**row)

    def list_case_runs(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        case_id: UUID,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[tuple[RunView, ...], str | None]:
        """List case runs only for active submitter/reviewer members."""
        if not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        cursor_value: tuple[datetime, UUID] | None = None
        if cursor:
            try:
                decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
                created, run_id = decoded.split("|", 1)
                cursor_value = (datetime.fromisoformat(created), UUID(run_id))
            except (ValueError, UnicodeDecodeError):
                raise ValueError("invalid_cursor") from None
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            member = connection.execute(
                "SELECT 1 FROM case_members WHERE tenant_id = %s AND case_id = %s "
                "AND actor_id = %s AND revoked_at IS NULL "
                "AND role IN ('submitter', 'reviewer')",
                (tenant_id, case_id, actor_id),
            ).fetchone()
            if member is None:
                raise BoundRunDenied("case_not_available")
            rows = connection.execute(
                """
                SELECT run.id, run.case_id, run.input_snapshot_id, run.status,
                       run.version, run.created_at,
                       (SELECT task.task_type FROM tasks AS task
                        WHERE task.tenant_id = run.tenant_id AND task.run_id = run.id
                        ORDER BY task.created_at LIMIT 1) AS task_type
                FROM audit_runs AS run
                WHERE run.tenant_id = %s AND run.case_id = %s
                  AND (%s::timestamptz IS NULL OR (run.created_at, run.id) < (%s, %s::uuid))
                ORDER BY run.created_at DESC, run.id DESC LIMIT %s
                """,
                (
                    tenant_id,
                    case_id,
                    cursor_value[0] if cursor_value else None,
                    cursor_value[0] if cursor_value else None,
                    cursor_value[1] if cursor_value else None,
                    limit + 1,
                ),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            tail = page[-1]
            raw = f"{tail['created_at'].isoformat()}|{tail['id']}".encode()
            next_cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        return tuple(RunView(**row) for row in page), next_cursor

    def get_run_results(self, tenant_id: UUID, actor_id: UUID, run_id: UUID) -> RunResultsView:
        """Read structured results only while every cited frozen source is still authorized."""
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            row = connection.execute(
                """
                SELECT run.id, run.case_id, run.input_snapshot_id, run.status,
                       run.version, run.created_at, member.role AS member_role,
                       (SELECT task.task_type FROM tasks AS task
                        WHERE task.tenant_id = run.tenant_id AND task.run_id = run.id
                        ORDER BY task.created_at LIMIT 1) AS task_type
                FROM audit_runs AS run
                JOIN case_members AS member
                  ON member.tenant_id = run.tenant_id AND member.case_id = run.case_id
                WHERE run.tenant_id = %s AND run.id = %s AND member.actor_id = %s
                  AND member.revoked_at IS NULL
                  AND member.role IN ('submitter', 'reviewer')
                """,
                (tenant_id, run_id, actor_id),
            ).fetchone()
            if row is None:
                raise BoundRunDenied("run_not_available")
            trace_events = connection.execute(
                """
                SELECT task_id, attempt, sequence, event_type, payload_sha256,
                       payload_bytes, previous_event_sha256, event_sha256,
                       input_tokens, output_tokens
                FROM task_execution_trace_events
                WHERE tenant_id = %s AND run_id = %s
                ORDER BY task_id, attempt, sequence
                """,
                (tenant_id, run_id),
            ).fetchall()
            reported_model_usage = _summarize_run_model_usage(trace_events)
            trace_events_by_attempt: dict[tuple[UUID, int], list[dict[str, Any]]] = {}
            for event in trace_events:
                key = (event["task_id"], event["attempt"])
                trace_events_by_attempt.setdefault(key, []).append(event)
            task_results = connection.execute(
                """
                SELECT tr.task_id, task.attempt, tr.result, tr.content_hash
                FROM task_results AS tr
                JOIN tasks AS task
                  ON task.tenant_id = tr.tenant_id AND task.id = tr.task_id
                WHERE tr.tenant_id = %s AND tr.run_id = %s
                    AND task.task_type = 'contract_review'
                ORDER BY tr.created_at, tr.task_id
                """,
                (tenant_id, run_id),
            ).fetchall()
            if not task_results:
                return RunResultsView(
                    RunView(
                        row["id"],
                        row["case_id"],
                        row["input_snapshot_id"],
                        row["status"],
                        row["version"],
                        row["created_at"],
                        row["task_type"],
                    ),
                    (),
                    reported_model_usage,
                    row["member_role"] == "reviewer",
                )
            if row["input_snapshot_id"] is None:
                raise BoundRunDenied("run_input_snapshot_missing")
            snapshot = connection.execute(
                "SELECT purpose, require_amount FROM input_snapshots "
                "WHERE tenant_id = %s AND case_id = %s AND id = %s",
                (tenant_id, row["case_id"], row["input_snapshot_id"]),
            ).fetchone()
            if snapshot is None:
                raise BoundRunDenied("run_input_snapshot_missing")
            frozen_source_ids = {
                source["document_version_id"]
                for source in connection.execute(
                    "SELECT document_version_id FROM input_snapshot_documents "
                    "WHERE tenant_id = %s AND snapshot_id = %s",
                    (tenant_id, row["input_snapshot_id"]),
                ).fetchall()
            }
            if not frozen_source_ids:
                raise RetrievalDenied("result_sources_missing")
            access = TaskAccess(
                tenant_id,
                row["case_id"],
                actor_id,
                snapshot["purpose"],
                snapshot["require_amount"],
                row["input_snapshot_id"],
            )
            projector = AuthorizationProjector(self._database_url)
            allowed = projector.indexed_documents_in_transaction(access, connection)
            results: list[dict[str, Any]] = []
            cited_document_ids: set[UUID] = set()
            for task_result in task_results:
                payload = task_result["result"]
                if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                    raise ValueError("stored_run_result_contract_invalid")
                if _task_result_content_hash(payload) != task_result["content_hash"]:
                    raise ValueError("stored_run_result_content_hash_invalid")
                _validate_result_execution_trace(
                    payload,
                    trace_events_by_attempt.get(
                        (task_result["task_id"], task_result["attempt"]), []
                    ),
                )
                for result in payload["results"]:
                    if not isinstance(result, dict) or not isinstance(result.get("evidence"), list):
                        raise ValueError("stored_run_result_contract_invalid")
                    result_sources: set[UUID] = set()
                    for evidence in result["evidence"]:
                        if not isinstance(evidence, dict):
                            raise ValueError("stored_run_result_contract_invalid")
                        if evidence.get("kind") == "source_record":
                            # External source records need their own current field/source
                            # authorization check. Until that projection exists, never
                            # expose a stored reference merely because its run is visible.
                            raise RetrievalDenied("result_source_not_authorized")
                        source_id = evidence.get("document_version_id")
                        if source_id is not None:
                            try:
                                result_sources.add(UUID(str(source_id)))
                            except ValueError as exc:
                                raise ValueError("stored_run_result_contract_invalid") from exc
                    if not result_sources:
                        if result.get("judgment") not in {
                            "needs_material",
                            "undetermined",
                            "human_review",
                        }:
                            raise RetrievalDenied("result_sources_missing")
                        result_sources = frozen_source_ids
                    cited_document_ids.update(result_sources)
                    projected_result = dict(result)
                    if result.get("research_review") is not None:
                        if not frozen_source_ids.issubset(allowed):
                            raise RetrievalDenied("result_source_not_authorized")
                        research = dict(result["research_review"])
                        projected_sources = []
                        for source in research.get("sources", []):
                            source = dict(source)
                            if source.get("origin") == "local":
                                current = connection.execute(
                                    "SELECT status FROM knowledge_documents WHERE tenant_id=%s "
                                    "AND id=%s AND content_sha256=%s",
                                    (tenant_id, UUID(source["document_id"]), source["content_sha256"]),
                                ).fetchone()
                                if current is None:
                                    raise RetrievalDenied("result_source_not_authorized")
                                source["current_status"] = current["status"]
                            projected_sources.append(source)
                        research["sources"] = projected_sources
                        projected_result["research_review"] = research
                    projected_result["task_result_sha256"] = task_result["content_hash"]
                    results.append(projected_result)
            if cited_document_ids and not cited_document_ids.issubset(allowed):
                raise RetrievalDenied("result_source_not_authorized")
            return RunResultsView(
                RunView(
                    row["id"],
                    row["case_id"],
                    row["input_snapshot_id"],
                    row["status"],
                    row["version"],
                    row["created_at"],
                    row["task_type"],
                ),
                tuple(results),
                reported_model_usage,
                row["member_role"] == "reviewer",
            )

    def get_upstream_comparison_results(
        self, tenant_id: UUID, actor_id: UUID, run_id: UUID
    ) -> UpstreamComparisonView:
        """Project a stored comparison only while its entire frozen pair stays authorized."""
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            row = connection.execute(
                """
                SELECT run.id, run.case_id, run.input_snapshot_id, run.status,
                       run.version, run.created_at,
                       (SELECT task.task_type FROM tasks AS task
                        WHERE task.tenant_id = run.tenant_id AND task.run_id = run.id
                        ORDER BY task.created_at LIMIT 1) AS task_type
                FROM audit_runs AS run
                JOIN case_members AS member
                  ON member.tenant_id = run.tenant_id AND member.case_id = run.case_id
                WHERE run.tenant_id = %s AND run.id = %s AND member.actor_id = %s
                  AND member.revoked_at IS NULL
                  AND member.role IN ('submitter', 'reviewer')
                """,
                (tenant_id, run_id, actor_id),
            ).fetchone()
            if row is None or row["task_type"] != "upstream_comparison":
                raise BoundRunDenied("upstream_comparison_not_available")
            if row["input_snapshot_id"] is None:
                raise BoundRunDenied("run_input_snapshot_missing")
            snapshot = connection.execute(
                "SELECT purpose, require_amount FROM input_snapshots "
                "WHERE tenant_id = %s AND case_id = %s AND id = %s",
                (tenant_id, row["case_id"], row["input_snapshot_id"]),
            ).fetchone()
            if (
                snapshot is None
                or snapshot["purpose"] != "upstream_comparison"
                or not snapshot["require_amount"]
            ):
                raise BoundRunDenied("upstream_comparison_snapshot_invalid")
            frozen_rows = connection.execute(
                "SELECT frozen.document_version_id, frozen.relation, frozen.source_sha256, "
                "frozen.parsed_sha256, frozen.parser_version, frozen.rendered_pdf_sha256, "
                "frozen.renderer_identity, document.source_name AS file_name "
                "FROM input_snapshot_documents AS frozen JOIN document_versions AS document "
                "ON document.tenant_id = frozen.tenant_id "
                "AND document.id = frozen.document_version_id "
                "AND document.source_sha256 = frozen.source_sha256 "
                "AND document.parsed_sha256 = frozen.parsed_sha256 "
                "AND document.parser_version = frozen.parser_version "
                "AND document.rendered_pdf_sha256 IS NOT DISTINCT FROM frozen.rendered_pdf_sha256 "
                "AND document.renderer_identity IS NOT DISTINCT FROM frozen.renderer_identity "
                "JOIN document_indexes AS indexed "
                "ON indexed.tenant_id = frozen.tenant_id "
                "AND indexed.document_version_id = frozen.document_version_id "
                "AND indexed.source_sha256 = frozen.source_sha256 "
                "AND indexed.pageindex_configuration_sha256 = "
                "frozen.pageindex_configuration_sha256 "
                "AND indexed.rendered_pdf_sha256 IS NOT DISTINCT FROM frozen.rendered_pdf_sha256 "
                "AND indexed.renderer_identity IS NOT DISTINCT FROM frozen.renderer_identity "
                "WHERE frozen.tenant_id = %s AND frozen.snapshot_id = %s",
                (tenant_id, row["input_snapshot_id"]),
            ).fetchall()
            frozen_count = connection.execute(
                "SELECT count(*) AS total FROM input_snapshot_documents "
                "WHERE tenant_id = %s AND snapshot_id = %s",
                (tenant_id, row["input_snapshot_id"]),
            ).fetchone()
            if frozen_count is None or len(frozen_rows) != frozen_count["total"]:
                raise RetrievalDenied("result_sources_missing")
            frozen_ids = {item["document_version_id"] for item in frozen_rows}
            upstream_ids = {
                item["document_version_id"]
                for item in frozen_rows
                if item["relation"] == "upstream"
            }
            main_ids = {
                item["document_version_id"]
                for item in frozen_rows
                if item["relation"] == "main"
            }
            if len(main_ids) != 1 or not upstream_ids:
                raise BoundRunDenied("upstream_comparison_frozen_pair_invalid")
            frozen_sources = {item["document_version_id"]: item for item in frozen_rows}
            access = TaskAccess(
                tenant_id,
                row["case_id"],
                actor_id,
                "upstream_comparison",
                True,
                row["input_snapshot_id"],
            )
            allowed = AuthorizationProjector(self._database_url).indexed_documents_in_transaction(
                access, connection
            )
            if not frozen_ids.issubset(allowed):
                raise RetrievalDenied("result_source_not_authorized")
            task_result = connection.execute(
                """
                SELECT result.task_id, task.attempt, result.result, result.content_hash
                FROM task_results AS result
                JOIN tasks AS task
                  ON task.tenant_id = result.tenant_id AND task.id = result.task_id
                WHERE result.tenant_id = %s AND result.run_id = %s
                  AND task.task_type = 'upstream_comparison'
                ORDER BY result.created_at DESC LIMIT 1
                """,
                (tenant_id, run_id),
            ).fetchone()
            stored_result = task_result["result"] if task_result is not None else None
            if task_result is not None:
                if (
                    not isinstance(stored_result, dict)
                    or _task_result_content_hash(stored_result) != task_result["content_hash"]
                ):
                    raise ValueError("stored_upstream_comparison_result_integrity_invalid")
                trace_rows = connection.execute(
                    """
                    SELECT sequence, event_type, payload_sha256, payload_bytes,
                           previous_event_sha256, event_sha256, input_tokens, output_tokens
                    FROM task_execution_trace_events
                    WHERE tenant_id = %s AND task_id = %s AND attempt = %s
                    ORDER BY sequence
                    """,
                    (tenant_id, task_result["task_id"], task_result["attempt"]),
                ).fetchall()
                try:
                    persisted_trace = ExecutionTraceSnapshot.from_events(
                        [
                            ExecutionTraceEvent.model_validate(event)
                            for event in trace_rows
                        ]
                    )
                    stored_trace = ExecutionTraceSnapshot.model_validate(
                        stored_result["execution_trace"]
                    )
                except (KeyError, TypeError, ValidationError, ValueError) as exc:
                    raise ValueError("stored_upstream_comparison_trace_invalid") from exc
                if stored_trace != persisted_trace:
                    raise ValueError("stored_upstream_comparison_trace_ledger_mismatch")
                pairs = stored_result.get("pairs")
                if (
                    stored_result.get("candidate_only") is not True
                    or stored_result.get("input_snapshot_id") != str(row["input_snapshot_id"])
                    or not isinstance(pairs, list)
                ):
                    raise ValueError("stored_upstream_comparison_result_invalid")
                pair_ids: set[UUID] = set()
                for pair in pairs:
                    if not isinstance(pair, dict):
                        raise ValueError("stored_upstream_comparison_result_invalid")
                    try:
                        upstream_id = UUID(str(pair["upstream_document_version_id"]))
                        procurement_id = UUID(str(pair["procurement_document_version_id"]))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError("stored_upstream_comparison_result_invalid") from exc
                    if procurement_id not in main_ids or upstream_id not in upstream_ids:
                        raise ValueError("stored_upstream_comparison_source_binding_invalid")
                    if (
                        pair.get("upstream_file_name")
                        != frozen_sources[upstream_id]["file_name"]
                        or pair.get("procurement_file_name")
                        != frozen_sources[procurement_id]["file_name"]
                    ):
                        raise ValueError("stored_upstream_comparison_file_binding_invalid")
                    categories = pair.get("categories")
                    if (
                        not isinstance(categories, list)
                        or [item.get("category") for item in categories if isinstance(item, dict)]
                        != [category.value for category in OBLIGATION_CATEGORY_ORDER]
                    ):
                        raise ValueError("stored_upstream_comparison_categories_invalid")
                    for category in categories:
                        evidence_items = category.get("evidence")
                        if not isinstance(evidence_items, list):
                            raise ValueError("stored_upstream_comparison_evidence_invalid")
                        state = category.get("state")
                        applicability = category.get("applicability")
                        coverage = category.get("coverage")
                        if state == "candidate":
                            if applicability is None or coverage is None:
                                raise ValueError("stored_upstream_comparison_assessment_invalid")
                            if applicability == "not_applicable":
                                if coverage != "not_applicable" or not category.get("limitations"):
                                    raise ValueError(
                                        "stored_upstream_comparison_assessment_invalid"
                                    )
                            elif applicability != "applicable" or coverage == "not_applicable":
                                raise ValueError("stored_upstream_comparison_assessment_invalid")
                        elif applicability is not None or coverage is not None:
                            raise ValueError("stored_upstream_comparison_assessment_invalid")
                        if state == "candidate" and applicability == "applicable":
                            if not any(
                                item.get("side") == "upstream"
                                and item.get("kind") == "document_span"
                                for item in evidence_items
                                if isinstance(item, dict)
                            ):
                                raise ValueError("stored_upstream_comparison_evidence_invalid")
                            required_kind = (
                                "absence_evidence" if coverage == "missing" else "document_span"
                            )
                            if not any(
                                item.get("side") == "procurement"
                                and item.get("kind") == required_kind
                                for item in evidence_items
                                if isinstance(item, dict)
                            ):
                                raise ValueError("stored_upstream_comparison_evidence_invalid")
                        for evidence in evidence_items:
                            if not isinstance(evidence, dict):
                                raise ValueError("stored_upstream_comparison_evidence_invalid")
                            try:
                                evidence_source_id = UUID(str(evidence["document_version_id"]))
                            except (KeyError, TypeError, ValueError) as exc:
                                raise ValueError(
                                    "stored_upstream_comparison_evidence_invalid"
                                ) from exc
                            expected_source_id = (
                                upstream_id if evidence.get("side") == "upstream" else procurement_id
                            )
                            if (
                                evidence.get("side") not in {"upstream", "procurement"}
                                or evidence_source_id != expected_source_id
                                or evidence.get("source_sha256")
                                != frozen_sources[evidence_source_id]["source_sha256"]
                                or evidence.get("parsed_sha256")
                                != frozen_sources[evidence_source_id]["parsed_sha256"]
                                or evidence.get("parser_version")
                                != frozen_sources[evidence_source_id]["parser_version"]
                                or evidence.get("index_source_sha256")
                                != (
                                    frozen_sources[evidence_source_id]["rendered_pdf_sha256"]
                                    or frozen_sources[evidence_source_id]["source_sha256"]
                                )
                                or evidence.get("renderer_identity")
                                != frozen_sources[evidence_source_id]["renderer_identity"]
                            ):
                                raise ValueError(
                                    "stored_upstream_comparison_evidence_source_invalid"
                                )
                            if evidence.get("kind") == "document_span":
                                quote = evidence.get("quote")
                                page = evidence.get("page")
                                start_offset = evidence.get("start_offset")
                                end_offset = evidence.get("end_offset")
                                if (
                                    not isinstance(quote, str)
                                    or not quote
                                    or isinstance(page, bool)
                                    or not isinstance(page, int)
                                    or page < 1
                                    or isinstance(start_offset, bool)
                                    or not isinstance(start_offset, int)
                                    or start_offset < 0
                                    or isinstance(end_offset, bool)
                                    or not isinstance(end_offset, int)
                                    or end_offset < start_offset
                                    or end_offset - start_offset != len(quote)
                                    or evidence.get("quote_sha256")
                                    != sha256(quote.encode("utf-8")).hexdigest()
                                ):
                                    raise ValueError(
                                        "stored_upstream_comparison_quote_binding_invalid"
                                    )
                            elif (
                                evidence.get("kind") != "absence_evidence"
                                or evidence.get("side") != "procurement"
                                or evidence.get("parser_coverage") != "complete"
                                or not isinstance(evidence.get("searched_pages"), list)
                                or not evidence["searched_pages"]
                                or any(
                                    isinstance(page, bool)
                                    or not isinstance(page, int)
                                    or page < 1
                                    for page in evidence["searched_pages"]
                                )
                                or len(evidence["searched_pages"])
                                != len(set(evidence["searched_pages"]))
                                or not isinstance(evidence.get("searched_sections"), list)
                                or not evidence["searched_sections"]
                                or any(
                                    not isinstance(item, str) or not item.strip()
                                    for item in evidence["searched_sections"]
                                )
                                or not isinstance(evidence.get("query_terms"), list)
                                or not evidence["query_terms"]
                                or any(
                                    not isinstance(item, str) or not item.strip()
                                    for item in evidence["query_terms"]
                                )
                            ):
                                raise ValueError(
                                    "stored_upstream_comparison_absence_scope_invalid"
                                )
                    pair_ids.add(upstream_id)
                if pair_ids != upstream_ids:
                    raise ValueError("stored_upstream_comparison_pair_set_incomplete")
            return UpstreamComparisonView(
                run=RunView(
                    row["id"],
                    row["case_id"],
                    row["input_snapshot_id"],
                    row["status"],
                    row["version"],
                    row["created_at"],
                    row["task_type"],
                ),
                result=stored_result,
            )

    def confirm_run_facts(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        run_id: UUID,
        confirmations: list[dict[str, Any]],
        reason: str,
        idempotency_key: str,
    ) -> FactSetSnapshotView:
        """Confirm cited candidates into a new immutable fact snapshot for this input."""
        if (
            not confirmations
            or len(confirmations) > 4
            or not reason.strip()
            or len(reason) > 4000
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None
        ):
            raise ValueError("invalid_fact_confirmation")
        predicates = [str(item.get("predicate", "")) for item in confirmations]
        if len(predicates) != len(set(predicates)):
            raise ValueError("duplicate_fact_confirmation")
        request_value = {
            "run_id": str(run_id),
            "reason": reason,
            "confirmations": sorted(
                [
                    {
                        "predicate": item["predicate"],
                        "value": item["value"],
                        "evidence_ids": sorted(str(value) for value in item["evidence_ids"]),
                    }
                    for item in confirmations
                ],
                key=lambda value: value["predicate"],
            ),
        }
        request_hash = sha256(
            json.dumps(request_value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                connection.execute(
                    "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),)
                )
                run = connection.execute(
                    """
                    SELECT run.id, run.case_id, run.input_snapshot_id, run.status,
                           snapshot.purpose, snapshot.require_amount
                    FROM audit_runs AS run
                    JOIN case_members AS member
                      ON member.tenant_id = run.tenant_id AND member.case_id = run.case_id
                    JOIN input_snapshots AS snapshot
                      ON snapshot.tenant_id = run.tenant_id AND snapshot.case_id = run.case_id
                     AND snapshot.id = run.input_snapshot_id
                    WHERE run.tenant_id = %s AND run.id = %s AND member.actor_id = %s
                      AND member.revoked_at IS NULL AND member.role = 'reviewer'
                    FOR UPDATE OF run
                    """,
                    (tenant_id, run_id, actor_id),
                ).fetchone()
                if run is None:
                    raise BoundRunDenied("run_not_available")
                if run["status"] != "completed" or run["purpose"] != "contract_review":
                    raise BoundRunConflict("fact_confirmation_run_not_complete")
                task_rows = connection.execute(
                    """
                    SELECT result, content_hash FROM task_results AS result
                    JOIN tasks AS task ON task.tenant_id = result.tenant_id
                      AND task.id = result.task_id
                    WHERE result.tenant_id = %s AND result.run_id = %s
                      AND task.task_type = 'contract_review'
                    """,
                    (tenant_id, run_id),
                ).fetchall()
                if not task_rows:
                    raise BoundRunConflict("fact_confirmation_candidates_missing")
                all_results: list[dict[str, Any]] = []
                for row in task_rows:
                    payload = row["result"]
                    if (
                        not isinstance(payload, dict)
                        or _task_result_content_hash(payload) != row["content_hash"]
                    ):
                        raise BoundRunConflict("fact_confirmation_result_integrity_invalid")
                    all_results.extend(payload.get("results", []))
                authorized_documents: set[UUID] = set()
                selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
                for confirmation in confirmations:
                    if (
                        confirmation["predicate"] == "contract.total_amount"
                        and not run["require_amount"]
                    ):
                        raise BoundRunDenied("fact_confirmation_field_not_authorized")
                    evidence_ids = [str(value) for value in confirmation["evidence_ids"]]
                    if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
                        raise BoundRunConflict("fact_confirmation_evidence_invalid")
                    matches = [
                        (result, candidate)
                        for result in all_results
                        for candidate in result.get("fact_candidates", [])
                        if candidate.get("predicate") == confirmation["predicate"]
                        and candidate.get("state") == "candidate"
                    ]
                    if len(matches) != 1 or set(matches[0][1].get("evidence_ids", [])) != set(
                        evidence_ids
                    ):
                        raise BoundRunConflict("fact_confirmation_candidate_mismatch")
                    candidate_result, candidate = matches[0]
                    evidence_by_id = {
                        str(evidence["evidence_id"]): evidence
                        for evidence in candidate_result.get("evidence", [])
                        if isinstance(evidence, dict) and evidence.get("evidence_id") is not None
                    }
                    if not set(evidence_ids).issubset(evidence_by_id):
                        raise BoundRunConflict("fact_confirmation_evidence_invalid")
                    for evidence_id in evidence_ids:
                        evidence = evidence_by_id.get(evidence_id)
                        if evidence is None or evidence.get("kind") != "document_span":
                            raise BoundRunConflict("fact_confirmation_evidence_invalid")
                        try:
                            authorized_documents.add(UUID(str(evidence["document_version_id"])))
                        except (KeyError, ValueError) as exc:
                            raise BoundRunConflict("fact_confirmation_evidence_invalid") from exc
                    selected.append((confirmation, candidate))

                access = TaskAccess(
                    tenant_id,
                    run["case_id"],
                    actor_id,
                    run["purpose"],
                    run["require_amount"],
                    run["input_snapshot_id"],
                )
                allowed = AuthorizationProjector(
                    self._database_url
                ).indexed_documents_in_transaction(access, connection)
                if not authorized_documents or not authorized_documents.issubset(allowed):
                    raise BoundRunDenied("fact_confirmation_source_not_authorized")

                # Idempotency must not bypass the current source authorization check.
                # A retry after a grant is revoked must fail closed just like a new command.
                existing = connection.execute(
                    "SELECT * FROM fact_set_snapshots WHERE tenant_id = %s "
                    "AND idempotency_key = %s",
                    (tenant_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["request_hash"] != request_hash
                        or existing["created_by"] != actor_id
                    ):
                        raise BoundRunConflict("fact_confirmation_idempotency_conflict")
                    if _fact_set_content_hash(existing["facts"]) != existing["content_hash"]:
                        raise BoundRunConflict("fact_set_snapshot_integrity_invalid")
                    return _fact_set_snapshot_view(existing)

                latest = connection.execute(
                    "SELECT version, facts, content_hash FROM fact_set_snapshots "
                    "WHERE tenant_id = %s "
                    "AND case_id = %s AND input_snapshot_id = %s "
                    "ORDER BY version DESC LIMIT 1",
                    (tenant_id, run["case_id"], run["input_snapshot_id"]),
                ).fetchone()
                facts = dict(latest["facts"]) if latest else {}
                if latest and _fact_set_content_hash(facts) != latest["content_hash"]:
                    raise BoundRunConflict("fact_set_snapshot_integrity_invalid")
                timestamp = datetime.now(UTC).isoformat()
                for confirmation, candidate in selected:
                    predicate = confirmation["predicate"]
                    facts[predicate] = {
                        "value": confirmation["value"],
                        "verification_status": "verified",
                        "candidate_value": candidate["value"],
                        "evidence_ids": sorted(
                            str(value) for value in confirmation["evidence_ids"]
                        ),
                        "source_run_id": str(run_id),
                        "confirmed_by": str(actor_id),
                        "confirmed_at": timestamp,
                        "confirmation_reason": reason,
                    }
                facts_hash = _fact_set_content_hash(facts)
                inserted = connection.execute(
                    """
                    INSERT INTO fact_set_snapshots
                      (tenant_id, case_id, input_snapshot_id, source_run_id, version, facts,
                       content_hash, created_by, reason, idempotency_key, request_hash)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        tenant_id,
                        run["case_id"],
                        run["input_snapshot_id"],
                        run_id,
                        (latest["version"] + 1) if latest else 1,
                        json.dumps(facts),
                        facts_hash,
                        actor_id,
                        reason,
                        idempotency_key,
                        request_hash,
                    ),
                ).fetchone()
                assert inserted is not None
                return _fact_set_snapshot_view(inserted)
        except psycopg.errors.SerializationFailure as exc:
            raise BoundRunConflict("concurrent_fact_confirmation") from exc
        except psycopg.errors.UniqueViolation as exc:
            raise BoundRunConflict("concurrent_fact_confirmation") from exc

    def enqueue(
        self,
        tenant_id: UUID,
        run_id: UUID,
        task_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> UUID:
        if not task_type or not idempotency_key:
            raise ValueError("task_type_and_idempotency_key_required")
        with self._connect(tenant_id) as connection:
            run = connection.execute(
                "SELECT status FROM audit_runs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, run_id),
            ).fetchone()
            if run is None or run["status"] not in {"queued", "running", "waiting"}:
                raise ValueError("run_not_active")
            row = connection.execute(
                """
                INSERT INTO tasks
                  (tenant_id, run_id, task_type, idempotency_key, payload, traceparent)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, task_type, idempotency_key) DO UPDATE
                  SET updated_at = tasks.updated_at
                WHERE tasks.run_id = EXCLUDED.run_id AND tasks.payload = EXCLUDED.payload
                RETURNING id
                """,
                (
                    tenant_id,
                    run_id,
                    task_type,
                    idempotency_key,
                    json.dumps(payload),
                    capture_traceparent(),
                ),
            ).fetchone()
            if row is None:
                raise ValueError("idempotency_key_conflict")
            return cast(UUID, row["id"])

    def claim(self, tenant_id: UUID, worker_id: str, lease_seconds: int = 60) -> LeasedTask | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("invalid_lease")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                WITH candidate AS (
                  SELECT id,
                    (EXTRACT(EPOCH FROM (clock_timestamp() -
                      GREATEST(next_run_at, COALESCE(lease_until, created_at)))) * 1000)
                      ::double precision AS queue_wait_ms
                  FROM tasks
                  WHERE next_run_at <= now()
                    AND (status = 'queued' OR
                         (status = 'running' AND lease_until < now()))
                    AND EXISTS (
                      SELECT 1 FROM audit_runs AS r
                      WHERE r.tenant_id = tasks.tenant_id AND r.id = tasks.run_id
                        AND r.status IN ('queued', 'running', 'waiting')
                    )
                  ORDER BY priority DESC, next_run_at, id
                  FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE tasks AS t
                SET status = 'running', attempt = t.attempt + 1,
                    lease_owner = %s,
                    lease_until = now() + make_interval(secs => %s),
                    fencing_token = t.fencing_token + 1,
                    updated_at = now()
                FROM candidate
                WHERE t.id = candidate.id AND t.tenant_id = %s
                RETURNING t.id, t.run_id, t.task_type, t.payload, t.attempt,
                          t.fencing_token, t.lease_owner, t.traceparent,
                          candidate.queue_wait_ms
                """,
                (worker_id, lease_seconds, tenant_id),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE audit_runs SET status = 'running' "
                "WHERE tenant_id = %s AND id = %s AND status IN ('queued', 'waiting')",
                (tenant_id, row["run_id"]),
            )
            return LeasedTask(**row)

    def complete(
        self,
        tenant_id: UUID,
        task: LeasedTask,
        worker_id: str,
        result: dict[str, Any],
    ) -> bool:
        serialized = _serialize_task_result(result)
        digest = sha256(serialized.encode("utf-8")).hexdigest()
        with self._connect(tenant_id) as connection:
            run = connection.execute(
                "SELECT status FROM audit_runs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, task.run_id),
            ).fetchone()
            if run is None or run["status"] not in {"queued", "running", "waiting"}:
                return False
            updated = connection.execute(
                """
                UPDATE tasks SET status = 'completed', lease_owner = NULL, lease_until = NULL,
                  updated_at = now()
                WHERE tenant_id = %s AND id = %s AND run_id = %s AND status = 'running'
                  AND lease_owner = %s AND fencing_token = %s AND lease_until > now()
                RETURNING id
                """,
                (tenant_id, task.id, task.run_id, worker_id, task.fencing_token),
            ).fetchone()
            if updated is None:
                return False
            connection.execute(
                """
                UPDATE audit_runs AS run SET status = 'completed'
                WHERE run.tenant_id = %s AND run.id = %s
                  AND NOT EXISTS (
                    SELECT 1 FROM tasks AS pending
                    WHERE pending.tenant_id = run.tenant_id AND pending.run_id = run.id
                      AND pending.status IN ('queued', 'running', 'waiting_human')
                  )
                """,
                (tenant_id, task.run_id),
            )
            connection.execute(
                """
                INSERT INTO task_results (tenant_id, task_id, run_id, result, content_hash)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                (tenant_id, task.id, task.run_id, serialized, digest),
            )
            if _price_review_required(result):
                human_task = connection.execute(
                    """
                    INSERT INTO human_tasks
                      (tenant_id, task_id, run_id, case_id, type, reason_code, required_role)
                    SELECT %s, %s, run.id, run.case_id, 'price_review',
                           'price_basis_requires_review', 'reviewer'
                    FROM audit_runs AS run
                    WHERE run.tenant_id = %s AND run.id = %s
                    RETURNING id
                    """,
                    (tenant_id, task.id, tenant_id, task.run_id),
                ).fetchone()
                assert human_task is not None
                connection.execute(
                    """
                    INSERT INTO outbox_events
                      (tenant_id, task_id, run_id, event_type, payload, traceparent)
                    VALUES (%s, %s, %s, 'human_task.created', %s::jsonb, %s)
                    """,
                    (
                        tenant_id,
                        task.id,
                        task.run_id,
                        json.dumps({"human_task_id": str(human_task["id"])}),
                        capture_traceparent() or task.traceparent,
                    ),
                )
            connection.execute(
                """
                INSERT INTO outbox_events
                  (tenant_id, task_id, run_id, event_type, payload, traceparent)
                VALUES (%s, %s, %s, 'task.completed', %s::jsonb, %s)
                """,
                (
                    tenant_id,
                    task.id,
                    task.run_id,
                    json.dumps({"task_id": str(task.id)}),
                    capture_traceparent() or task.traceparent,
                ),
            )
            return True

    def heartbeat(
        self, tenant_id: UUID, task: LeasedTask, worker_id: str, lease_seconds: int
    ) -> bool:
        """Extend only the still-live lease held by this fenced worker."""
        if not worker_id or lease_seconds <= 0:
            raise ValueError("invalid_lease")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE tasks SET lease_until = now() + make_interval(secs => %s),
                    updated_at = now()
                WHERE tenant_id = %s AND id = %s AND run_id = %s AND status = 'running'
                  AND lease_owner = %s AND fencing_token = %s AND lease_until > now()
                RETURNING id
                """,
                (lease_seconds, tenant_id, task.id, task.run_id, worker_id, task.fencing_token),
            ).fetchone()
            return row is not None

    def retry(
        self, tenant_id: UUID, task: LeasedTask, worker_id: str, error: str, delay_seconds: int
    ) -> bool:
        if delay_seconds < 0:
            raise ValueError("invalid_retry_delay")
        with self._connect(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE tasks SET status = 'queued', lease_owner = NULL, lease_until = NULL,
                  next_run_at = now() + make_interval(secs => %s), last_error = %s,
                  updated_at = now()
                WHERE tenant_id = %s AND id = %s AND run_id = %s AND status = 'running'
                  AND lease_owner = %s AND fencing_token = %s AND lease_until > now()
                RETURNING id
                """,
                (
                    delay_seconds,
                    error,
                    tenant_id,
                    task.id,
                    task.run_id,
                    worker_id,
                    task.fencing_token,
                ),
            ).fetchone()
            return row is not None

    def fail(self, tenant_id: UUID, task: LeasedTask, worker_id: str, error_code: str) -> bool:
        """Permanently fail a leased task and emit its event in one transaction."""
        if not error_code or len(error_code) > 128:
            raise ValueError("invalid_error_code")
        with self._connect(tenant_id) as connection:
            run = connection.execute(
                "SELECT status FROM audit_runs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, task.run_id),
            ).fetchone()
            if run is None or run["status"] not in {"queued", "running", "waiting"}:
                return False
            row = connection.execute(
                """
                UPDATE tasks SET status = 'failed', lease_owner = NULL, lease_until = NULL,
                  last_error = %s, updated_at = now()
                WHERE tenant_id = %s AND id = %s AND run_id = %s AND status = 'running'
                  AND lease_owner = %s AND fencing_token = %s AND lease_until > now()
                RETURNING id
                """,
                (error_code, tenant_id, task.id, task.run_id, worker_id, task.fencing_token),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE audit_runs SET status = 'failed' "
                "WHERE tenant_id = %s AND id = %s AND status IN ('queued', 'running', 'waiting')",
                (tenant_id, task.run_id),
            )
            connection.execute(
                """
                INSERT INTO outbox_events
                  (tenant_id, task_id, run_id, event_type, payload, traceparent)
                VALUES (%s, %s, %s, 'task.failed', %s::jsonb, %s)
                """,
                (
                    tenant_id,
                    task.id,
                    task.run_id,
                    json.dumps({"task_id": str(task.id), "error_code": error_code}),
                    capture_traceparent() or task.traceparent,
                ),
            )
            return True

    def wait_for_fact_review(
        self,
        tenant_id: UUID,
        task: LeasedTask,
        worker_id: str,
        reason_code: str,
        required_role: str,
    ) -> UUID | None:
        """Release a leased task into a durable, role-bound fact-review wait."""
        if (
            not worker_id
            or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason_code) is None
            or not required_role
            or required_role != required_role.strip()
            or len(required_role) > 64
        ):
            raise ValueError("invalid_fact_review_request")
        with self._connect(tenant_id) as connection:
            run = connection.execute(
                "SELECT status FROM audit_runs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, task.run_id),
            ).fetchone()
            if run is None or run["status"] not in {"queued", "running", "waiting"}:
                return None
            updated = connection.execute(
                """
                UPDATE tasks SET status = 'waiting_human', lease_owner = NULL,
                    lease_until = NULL, fencing_token = fencing_token + 1,
                    updated_at = now()
                WHERE tenant_id = %s AND id = %s AND run_id = %s AND status = 'running'
                  AND lease_owner = %s AND fencing_token = %s AND lease_until > now()
                RETURNING run_id
                """,
                (tenant_id, task.id, task.run_id, worker_id, task.fencing_token),
            ).fetchone()
            if updated is None:
                return None
            connection.execute(
                "UPDATE audit_runs SET status = 'waiting' "
                "WHERE tenant_id = %s AND id = %s AND status IN ('queued', 'running')",
                (tenant_id, task.run_id),
            )
            human_task = connection.execute(
                """
                INSERT INTO human_tasks
                  (tenant_id, task_id, run_id, case_id, type, reason_code, required_role)
                SELECT %s, %s, r.id, r.case_id, 'fact_review', %s, %s
                FROM audit_runs AS r WHERE r.tenant_id = %s AND r.id = %s
                RETURNING id
                """,
                (tenant_id, task.id, reason_code, required_role, tenant_id, task.run_id),
            ).fetchone()
            assert human_task is not None
            connection.execute(
                """
                INSERT INTO outbox_events
                  (tenant_id, task_id, run_id, event_type, payload, traceparent)
                VALUES (%s, %s, %s, 'human_task.created', %s::jsonb, %s)
                """,
                (
                    tenant_id,
                    task.id,
                    task.run_id,
                    json.dumps({"human_task_id": str(human_task["id"])}),
                    capture_traceparent() or task.traceparent,
                ),
            )
            return cast(UUID, human_task["id"])

    def resolve_fact_review(
        self,
        tenant_id: UUID,
        human_task_id: UUID,
        actor_id: UUID,
        expected_version: int,
        reason: str,
        idempotency_key: str,
    ) -> HumanTaskView | None:
        """Record an authorized fact confirmation and requeue; never decide the check result."""
        if (
            expected_version <= 0
            or not reason.strip()
            or len(reason) > 4000
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None
        ):
            raise ValueError("invalid_fact_review_decision")
        request_hash = sha256(f"{expected_version}\0confirm_fact\0{reason}".encode()).hexdigest()
        with self._connect(tenant_id) as connection:
            human_ref = connection.execute(
                "SELECT run_id FROM human_tasks WHERE tenant_id = %s AND id = %s",
                (tenant_id, human_task_id),
            ).fetchone()
            if human_ref is None:
                return None
            run = connection.execute(
                "SELECT status FROM audit_runs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, human_ref["run_id"]),
            ).fetchone()
            if run is None:
                return None
            human_task = connection.execute(
                "SELECT * FROM human_tasks WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, human_task_id),
            ).fetchone()
            if human_task is None:
                return None
            if human_task["type"] != "fact_review":
                return None
            authorized = connection.execute(
                """
                SELECT 1 FROM case_members
                WHERE tenant_id = %s AND case_id = %s AND actor_id = %s
                  AND role = %s AND revoked_at IS NULL
                """,
                (
                    tenant_id,
                    human_task["case_id"],
                    actor_id,
                    human_task["required_role"],
                ),
            ).fetchone()
            if authorized is None:
                return None
            if human_task["status"] in {"cancelled", "superseded"}:
                return None
            if human_task["status"] == "completed":
                if (
                    human_task["decided_by"] == actor_id
                    and human_task["decision_idempotency_key"] == idempotency_key
                    and human_task["decision_request_hash"] == request_hash
                ):
                    return _human_task_view(human_task)
                raise HumanTaskConflict("human_task_already_decided")
            if human_task["status"] != "open" or human_task["version"] != expected_version:
                raise HumanTaskConflict("human_task_version_conflict")
            if run["status"] not in {"queued", "running", "waiting"}:
                raise HumanTaskConflict("human_task_run_not_active")
            task = connection.execute(
                """
                UPDATE tasks SET status = 'queued', next_run_at = now(), updated_at = now()
                WHERE tenant_id = %s AND id = %s AND status = 'waiting_human'
                RETURNING id
                """,
                (tenant_id, human_task["task_id"]),
            ).fetchone()
            if task is None:
                raise HumanTaskConflict("human_task_task_not_waiting")
            connection.execute(
                "UPDATE audit_runs SET status = 'queued' "
                "WHERE tenant_id = %s AND id = %s AND status = 'waiting'",
                (tenant_id, human_ref["run_id"]),
            )
            updated = connection.execute(
                """
                UPDATE human_tasks SET status = 'completed', decision = 'confirm_fact',
                    decision_reason = %s, decided_by = %s, decided_at = now(),
                    decision_idempotency_key = %s, decision_request_hash = %s,
                    version = version + 1
                WHERE tenant_id = %s AND id = %s
                RETURNING *
                """,
                (reason, actor_id, idempotency_key, request_hash, tenant_id, human_task_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO outbox_events
                  (tenant_id, task_id, run_id, event_type, payload, traceparent)
                VALUES (%s, %s, %s, 'human_task.completed', %s::jsonb, %s)
                """,
                (
                    tenant_id,
                    human_task["task_id"],
                    human_task["run_id"],
                    json.dumps({"human_task_id": str(human_task_id)}),
                    capture_traceparent(),
                ),
            )
            assert updated is not None
            return _human_task_view(updated)

    def resolve_price_review(
        self,
        tenant_id: UUID,
        human_task_id: UUID,
        actor_id: UUID,
        expected_version: int,
        decision: Literal["record_review", "request_material"],
        reason: str,
        idempotency_key: str,
    ) -> HumanTaskView | None:
        if (
            expected_version <= 0
            or decision not in {"record_review", "request_material"}
            or not reason.strip()
            or len(reason) > 4000
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None
        ):
            raise ValueError("invalid_price_review_decision")
        request_hash = sha256(
            f"{expected_version}\0{decision}\0{reason}".encode()
        ).hexdigest()
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),)
            )
            reference = connection.execute(
                "SELECT run_id FROM human_tasks WHERE tenant_id = %s AND id = %s",
                (tenant_id, human_task_id),
            ).fetchone()
            if reference is None:
                return None
            run = connection.execute(
                """
                SELECT run.id, run.case_id, run.input_snapshot_id, run.status,
                       snapshot.purpose, snapshot.require_amount
                FROM audit_runs AS run
                JOIN input_snapshots AS snapshot
                  ON snapshot.tenant_id = run.tenant_id AND snapshot.case_id = run.case_id
                 AND snapshot.id = run.input_snapshot_id
                JOIN case_members AS member
                  ON member.tenant_id = run.tenant_id AND member.case_id = run.case_id
                WHERE run.tenant_id = %s AND run.id = %s AND member.actor_id = %s
                  AND member.role = 'reviewer' AND member.revoked_at IS NULL
                FOR UPDATE OF run
                """,
                (tenant_id, reference["run_id"], actor_id),
            ).fetchone()
            human_task = connection.execute(
                "SELECT * FROM human_tasks WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, human_task_id),
            ).fetchone()
            if run is None or human_task is None or human_task["type"] != "price_review":
                return None
            if run["status"] != "completed" or run["purpose"] != "contract_review":
                raise HumanTaskConflict("price_review_run_not_complete")
            result_rows = connection.execute(
                """
                SELECT result, content_hash FROM task_results
                WHERE tenant_id = %s AND run_id = %s
                """,
                (tenant_id, run["id"]),
            ).fetchall()
            price_results: list[dict[str, Any]] = []
            for row in result_rows:
                payload = row["result"]
                if (
                    not isinstance(payload, dict)
                    or _task_result_content_hash(payload) != row["content_hash"]
                ):
                    raise HumanTaskConflict("price_review_result_integrity_invalid")
                if not _price_review_required(payload):
                    continue
                price_results.extend(
                    item
                    for item in payload.get("results", [])
                    if isinstance(item, dict) and item.get("rule_id") == "legal.price.definition"
                )
            if len(price_results) != 1:
                raise HumanTaskConflict("price_review_result_missing_or_ambiguous")
            price_result = price_results[0]
            evidence_by_id = {
                str(item["evidence_id"]): item
                for item in price_result.get("evidence", [])
                if isinstance(item, dict) and item.get("evidence_id") is not None
            }
            evidence_ids = {
                str(evidence_id)
                for candidate in price_result.get("slot_candidates", [])
                if isinstance(candidate, dict)
                for evidence_id in candidate.get("evidence_ids", [])
            }
            authorized_documents: set[UUID] = set()
            for evidence_id in evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    raise HumanTaskConflict("price_review_evidence_binding_invalid")
                if evidence.get("kind") != "document_span":
                    continue
                try:
                    authorized_documents.add(UUID(str(evidence["document_version_id"])))
                except (KeyError, ValueError) as exc:
                    raise HumanTaskConflict("price_review_evidence_binding_invalid") from exc
            access = TaskAccess(
                tenant_id,
                run["case_id"],
                actor_id,
                run["purpose"],
                run["require_amount"],
                run["input_snapshot_id"],
            )
            allowed_documents = AuthorizationProjector(
                self._database_url
            ).indexed_documents_in_transaction(access, connection)
            if not authorized_documents or not authorized_documents.issubset(allowed_documents):
                raise BoundRunDenied("price_review_source_not_authorized")
            if human_task["status"] == "completed":
                if (
                    human_task["decided_by"] == actor_id
                    and human_task["decision_idempotency_key"] == idempotency_key
                    and human_task["decision_request_hash"] == request_hash
                ):
                    return _human_task_view(human_task)
                raise HumanTaskConflict("human_task_already_decided")
            if human_task["status"] != "open" or human_task["version"] != expected_version:
                raise HumanTaskConflict("human_task_version_conflict")
            updated = connection.execute(
                """
                UPDATE human_tasks SET status = 'completed', decision = %s,
                    decision_reason = %s, decided_by = %s, decided_at = now(),
                    decision_idempotency_key = %s, decision_request_hash = %s,
                    version = version + 1
                WHERE tenant_id = %s AND id = %s
                RETURNING *
                """,
                (
                    decision,
                    reason.strip(),
                    actor_id,
                    idempotency_key,
                    request_hash,
                    tenant_id,
                    human_task_id,
                ),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO outbox_events
                  (tenant_id, task_id, run_id, event_type, payload, traceparent)
                VALUES (%s, %s, %s, 'human_task.completed', %s::jsonb, %s)
                """,
                (
                    tenant_id,
                    human_task["task_id"],
                    run["id"],
                    json.dumps({"human_task_id": str(human_task_id), "decision": decision}),
                    capture_traceparent(),
                ),
            )
            assert updated is not None
            return _human_task_view(updated)

    def list_human_tasks(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        *,
        case_id: UUID | None = None,
        run_id: UUID | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[tuple[HumanTaskView, ...], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        cursor_value: tuple[datetime, UUID] | None = None
        if cursor:
            try:
                decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
                created, task_id = decoded.split("|", 1)
                cursor_value = (datetime.fromisoformat(created), UUID(task_id))
            except (ValueError, UnicodeDecodeError):
                raise ValueError("invalid_cursor") from None
        with self._connect(tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT h.* FROM human_tasks AS h JOIN case_members AS m
                  ON m.tenant_id = h.tenant_id AND m.case_id = h.case_id
                WHERE h.tenant_id = %s AND m.actor_id = %s AND m.revoked_at IS NULL
                  AND m.role = h.required_role
                  AND (%s::uuid IS NULL OR h.case_id = %s)
                  AND (%s::uuid IS NULL OR h.run_id = %s)
                  AND (%s::text IS NULL OR h.status = %s)
                  AND (%s::timestamptz IS NULL OR (h.created_at, h.id) < (%s, %s::uuid))
                ORDER BY h.created_at DESC, h.id DESC LIMIT %s
                """,
                (
                    tenant_id,
                    actor_id,
                    case_id,
                    case_id,
                    run_id,
                    run_id,
                    status,
                    status,
                    cursor_value[0] if cursor_value else None,
                    cursor_value[0] if cursor_value else None,
                    cursor_value[1] if cursor_value else None,
                    limit + 1,
                ),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            tail = page[-1]
            raw = f"{tail['created_at'].isoformat()}|{tail['id']}".encode()
            next_cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        return tuple(_human_task_view(row) for row in page), next_cursor

    def cancel(self, tenant_id: UUID, task_id: UUID) -> bool:
        with self._connect(tenant_id) as connection:
            task = connection.execute(
                "SELECT run_id FROM tasks WHERE tenant_id = %s AND id = %s",
                (tenant_id, task_id),
            ).fetchone()
            if task is None:
                return False
            connection.execute(
                "SELECT id FROM audit_runs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, task["run_id"]),
            )
            row = connection.execute(
                """
                UPDATE tasks SET status = 'cancelled', lease_owner = NULL, lease_until = NULL,
                  fencing_token = fencing_token + 1, updated_at = now()
                WHERE tenant_id = %s AND id = %s
                  AND status IN ('queued', 'running', 'waiting_human')
                RETURNING id
                """,
                (tenant_id, task_id),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE human_tasks SET status = 'cancelled', version = version + 1 "
                "WHERE tenant_id = %s AND task_id = %s AND status = 'open'",
                (tenant_id, task_id),
            )
            return True

    def cancel_run(self, tenant_id: UUID, run_id: UUID) -> bool:
        """Idempotently cancel an active run and invalidate its unfinished leases."""
        return self._terminate_run(tenant_id, run_id, "cancelled")

    def supersede_run(self, tenant_id: UUID, run_id: UUID) -> bool:
        """Retire old work; creating a new version-bound run is a separate command."""
        return self._terminate_run(tenant_id, run_id, "superseded")

    def _terminate_run(
        self, tenant_id: UUID, run_id: UUID, terminal_status: Literal["cancelled", "superseded"]
    ) -> bool:
        with self._connect(tenant_id) as connection:
            run = connection.execute(
                "SELECT status FROM audit_runs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, run_id),
            ).fetchone()
            if run is None:
                return False
            if run["status"] == terminal_status:
                return True
            active_statuses = {"queued", "running", "waiting"}
            if terminal_status == "superseded":
                active_statuses.add("completed")
            if run["status"] not in active_statuses:
                return False
            connection.execute(
                "UPDATE audit_runs SET status = %s, version = version + 1 "
                "WHERE tenant_id = %s AND id = %s",
                (terminal_status, tenant_id, run_id),
            )
            connection.execute(
                """
                UPDATE tasks SET status = %s, lease_owner = NULL, lease_until = NULL,
                    fencing_token = fencing_token + 1, updated_at = now()
                WHERE tenant_id = %s AND run_id = %s
                  AND status IN ('queued', 'running', 'waiting_human')
                """,
                (terminal_status, tenant_id, run_id),
            )
            connection.execute(
                "UPDATE human_tasks SET status = %s, version = version + 1 "
                "WHERE tenant_id = %s AND run_id = %s AND status = 'open'",
                (terminal_status, tenant_id, run_id),
            )
            return True


def _human_task_view(row: dict[str, Any]) -> HumanTaskView:
    return HumanTaskView(
        id=row["id"],
        case_id=row["case_id"],
        run_id=row["run_id"],
        type=row["type"],
        status=row["status"],
        reason_code=row["reason_code"],
        required_role=row["required_role"],
        version=row["version"],
        decision=row["decision"],
        created_at=row["created_at"],
    )


def _fact_set_snapshot_view(row: dict[str, Any]) -> FactSetSnapshotView:
    if _fact_set_content_hash(row["facts"]) != row["content_hash"]:
        raise BoundRunConflict("fact_set_snapshot_integrity_invalid")
    return FactSetSnapshotView(
        id=row["id"],
        case_id=row["case_id"],
        input_snapshot_id=row["input_snapshot_id"],
        source_run_id=row["source_run_id"],
        version=row["version"],
        facts=row["facts"],
        content_hash=row["content_hash"],
        created_by=row["created_by"],
        reason=row["reason"],
        created_at=row["created_at"],
    )
