"""Content-free integrity trace for model and authorized tool exchanges."""

from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

ZERO_HASH = "0" * 64
MAX_TRACE_EVENTS = 32
MAX_TRACE_PAYLOAD_BYTES = 2_000_000
TraceEventType = Literal["model_request", "model_response", "tool_exchange"]


class ExecutionTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    event_type: TraceEventType
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_bytes: int = Field(ge=0, le=MAX_TRACE_PAYLOAD_BYTES)
    previous_event_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    event_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_token_usage(self) -> ExecutionTraceEvent:
        if (self.input_tokens is None) != (self.output_tokens is None) or (
            self.event_type != "model_response"
            and (self.input_tokens is not None or self.output_tokens is not None)
        ):
            raise ValueError("execution_trace_token_usage_invalid")
        return self

    @staticmethod
    def digest(
        sequence: int,
        event_type: TraceEventType,
        payload_sha256: str,
        payload_bytes: int,
        previous_event_sha256: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> str:
        event = {
            "sequence": sequence,
            "event_type": event_type,
            "payload_sha256": payload_sha256,
            "payload_bytes": payload_bytes,
            "previous_event_sha256": previous_event_sha256,
        }
        if input_tokens is not None or output_tokens is not None:
            if (
                event_type != "model_response"
                or isinstance(input_tokens, bool)
                or not isinstance(input_tokens, int)
                or input_tokens < 0
                or isinstance(output_tokens, bool)
                or not isinstance(output_tokens, int)
                or output_tokens < 0
            ):
                raise ValueError("execution_trace_token_usage_invalid")
            event["input_tokens"] = input_tokens
            event["output_tokens"] = output_tokens
        serialized = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(serialized).hexdigest()


class ModelUsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_responses: StrictInt = Field(ge=1)
    usage_reports: StrictInt = Field(ge=0)
    input_tokens: StrictInt | None = None
    output_tokens: StrictInt | None = None
    complete: StrictBool

    @model_validator(mode="after")
    def validate_aggregate(self) -> ModelUsageSummary:
        if (
            self.usage_reports > self.model_responses
            or self.complete != (self.usage_reports == self.model_responses)
            or (self.usage_reports == 0)
            != (self.input_tokens is None and self.output_tokens is None)
            or (self.usage_reports > 0)
            != (self.input_tokens is not None and self.output_tokens is not None)
        ):
            raise ValueError("execution_trace_token_usage_summary_invalid")
        return self


class RunModelUsageSummary(BaseModel):
    """Aggregate provider-reported usage across all persisted run attempts."""

    model_config = ConfigDict(extra="forbid")

    model_requests: StrictInt = Field(ge=0)
    model_responses: StrictInt = Field(ge=0)
    usage_reports: StrictInt = Field(ge=0)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    complete: StrictBool

    @model_validator(mode="after")
    def validate_aggregate(self) -> RunModelUsageSummary:
        if (
            self.usage_reports > self.model_responses
            or self.model_responses > self.model_requests
            or self.complete != (self.model_requests == self.model_responses == self.usage_reports)
            or (self.usage_reports == 0)
            != (self.input_tokens is None and self.output_tokens is None)
            or (self.usage_reports > 0)
            != (self.input_tokens is not None and self.output_tokens is not None)
        ):
            raise ValueError("run_execution_trace_usage_summary_invalid")
        return self


class ExecutionTraceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    events: list[ExecutionTraceEvent] = Field(max_length=MAX_TRACE_EVENTS)
    trace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_usage: ModelUsageSummary | None = None

    @model_validator(mode="after")
    def verify_chain(self) -> ExecutionTraceSnapshot:
        previous = ZERO_HASH
        for sequence, event in enumerate(self.events, start=1):
            if (
                event.sequence != sequence
                or event.previous_event_sha256 != previous
                or event.event_sha256
                != ExecutionTraceEvent.digest(
                    event.sequence,
                    event.event_type,
                    event.payload_sha256,
                    event.payload_bytes,
                    event.previous_event_sha256,
                    event.input_tokens,
                    event.output_tokens,
                )
            ):
                raise ValueError("execution_trace_chain_invalid")
            previous = event.event_sha256
        if self.trace_sha256 != previous:
            raise ValueError("execution_trace_head_invalid")
        expected_usage = _summarize_model_usage(self.events)
        if self.model_usage is not None and self.model_usage != expected_usage:
            raise ValueError("execution_trace_token_usage_summary_invalid")
        return self

    @classmethod
    def from_events(
        cls, events: tuple[ExecutionTraceEvent, ...] | list[ExecutionTraceEvent]
    ) -> ExecutionTraceSnapshot:
        immutable_events = tuple(events)
        return cls(
            events=list(immutable_events),
            trace_sha256=immutable_events[-1].event_sha256 if immutable_events else ZERO_HASH,
            model_usage=_summarize_model_usage(immutable_events),
        )


class ExecutionTraceRecorder:
    """Build a hash chain while keeping raw model/tool payloads only in memory."""

    def __init__(self, *, event_sink: Callable[[ExecutionTraceEvent], None] | None = None) -> None:
        self._events: list[ExecutionTraceEvent] = []
        self._event_sink = event_sink

    def record_payload(
        self,
        event_type: TraceEventType,
        payload: Any,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> ExecutionTraceEvent:
        serialized = json.dumps(
            _json_value(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(serialized) > MAX_TRACE_PAYLOAD_BYTES:
            raise ValueError("execution_trace_payload_too_large")
        return self.record_digest(
            event_type,
            sha256(serialized).hexdigest(),
            len(serialized),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def record_digest(
        self,
        event_type: TraceEventType,
        payload_sha256: str,
        payload_bytes: int,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> ExecutionTraceEvent:
        if len(self._events) >= MAX_TRACE_EVENTS:
            raise ValueError("execution_trace_event_limit_exceeded")
        previous = self._events[-1].event_sha256 if self._events else ZERO_HASH
        sequence = len(self._events) + 1
        event_sha256 = ExecutionTraceEvent.digest(
            sequence,
            event_type,
            payload_sha256,
            payload_bytes,
            previous,
            input_tokens,
            output_tokens,
        )
        event = ExecutionTraceEvent(
            sequence=sequence,
            event_type=event_type,
            payload_sha256=payload_sha256,
            payload_bytes=payload_bytes,
            previous_event_sha256=previous,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            event_sha256=event_sha256,
        )
        self._events.append(event)
        if self._event_sink is not None:
            self._event_sink(event)
        return event

    def snapshot(self) -> dict[str, Any]:
        return ExecutionTraceSnapshot.from_events(self._events).model_dump(
            mode="json", exclude_none=True
        )


def _summarize_model_usage(
    events: tuple[ExecutionTraceEvent, ...] | list[ExecutionTraceEvent],
) -> ModelUsageSummary | None:
    model_responses = [event for event in events if event.event_type == "model_response"]
    if not model_responses:
        return None
    reported = [event for event in model_responses if event.input_tokens is not None]
    return ModelUsageSummary(
        model_responses=len(model_responses),
        usage_reports=len(reported),
        input_tokens=sum(event.input_tokens or 0 for event in reported) if reported else None,
        output_tokens=sum(event.output_tokens or 0 for event in reported) if reported else None,
        complete=len(reported) == len(model_responses),
    )


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("execution_trace_payload_invalid")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError("execution_trace_payload_invalid")
