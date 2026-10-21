import asyncio

import pytest
from agentscope.message import TextBlock
from agentscope.model import ChatResponse, ChatUsage
from pydantic import ValidationError

from contract_app.agent.trace_middleware import ExecutionTraceMiddleware
from contract_app.workflow.execution_trace import (
    ExecutionTraceRecorder,
    ExecutionTraceSnapshot,
    RunModelUsageSummary,
)
from contract_app.workflow.job_store import _validate_result_execution_trace


def test_execution_trace_chains_content_hashes_without_storing_payload() -> None:
    recorder = ExecutionTraceRecorder()
    recorder.record_payload("model_request", {"messages": [{"content": "sensitive contract text"}]})
    recorder.record_payload("tool_exchange", {"quote": "sensitive contract text"})
    snapshot = recorder.snapshot()

    assert snapshot["schema_version"] == 1
    assert [event["sequence"] for event in snapshot["events"]] == [1, 2]
    assert snapshot["events"][1]["previous_event_sha256"] == snapshot["events"][0]["event_sha256"]
    assert "sensitive contract text" not in repr(snapshot)
    assert ExecutionTraceSnapshot.model_validate(snapshot).trace_sha256 == snapshot["trace_sha256"]


def test_execution_trace_rejects_tampered_chain() -> None:
    recorder = ExecutionTraceRecorder()
    recorder.record_payload("model_request", {"messages": []})
    snapshot = recorder.snapshot()
    snapshot["events"][0]["payload_sha256"] = "a" * 64

    with pytest.raises(ValidationError, match="execution_trace_chain_invalid"):
        ExecutionTraceSnapshot.model_validate(snapshot)


def test_execution_trace_event_sink_receives_ordered_content_free_events() -> None:
    stored = []
    recorder = ExecutionTraceRecorder(event_sink=stored.append)
    recorder.record_payload("model_request", {"messages": [{"content": "private text"}]})
    recorder.record_payload("model_response", {"status": "ok"}, input_tokens=4, output_tokens=2)

    assert [event.sequence for event in stored] == [1, 2]
    assert stored[1].previous_event_sha256 == stored[0].event_sha256
    assert "private text" not in repr(stored)
    stored_payloads = [event.model_dump(mode="json", exclude_none=True) for event in stored]
    assert stored_payloads == recorder.snapshot()["events"]


def test_successful_result_trace_is_bound_to_persisted_attempt_events() -> None:
    recorder = ExecutionTraceRecorder()
    recorder.record_payload("model_request", {"messages": []})
    recorder.record_payload("model_response", {"status": "ok"}, input_tokens=12, output_tokens=4)
    snapshot = recorder.snapshot()
    persisted_events = [
        {
            **event,
            "input_tokens": event.get("input_tokens"),
            "output_tokens": event.get("output_tokens"),
        }
        for event in snapshot["events"]
    ]

    _validate_result_execution_trace({"results": [{"execution_trace": snapshot}]}, persisted_events)

    tampered = ExecutionTraceRecorder()
    tampered.record_payload("model_request", {"messages": [{"content": "different"}]})
    tampered.record_payload("model_response", {"status": "ok"}, input_tokens=12, output_tokens=4)
    with pytest.raises(ValueError, match="stored_execution_trace_ledger_mismatch"):
        _validate_result_execution_trace(
            {"results": [{"execution_trace": tampered.snapshot()}]}, persisted_events
        )


def test_successful_result_trace_crosscheck_preserves_legacy_results_without_ledger() -> None:
    _validate_result_execution_trace({"legacy_result": True}, [])

    recorder = ExecutionTraceRecorder()
    recorder.record_payload("model_request", {"messages": []})
    persisted_events = [
        {
            **event,
            "input_tokens": event.get("input_tokens"),
            "output_tokens": event.get("output_tokens"),
        }
        for event in recorder.snapshot()["events"]
    ]
    with pytest.raises(ValueError, match="stored_execution_trace_missing"):
        _validate_result_execution_trace({"results": [{"execution_trace": None}]}, persisted_events)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            {
                "model_requests": 2,
                "model_responses": 1,
                "usage_reports": 1,
                "input_tokens": 10,
                "output_tokens": 2,
                "complete": True,
            },
            "run_execution_trace_usage_summary_invalid",
        ),
        (
            {
                "model_requests": 1,
                "model_responses": 1,
                "usage_reports": 1,
                "input_tokens": 10,
                "output_tokens": 2,
                "complete": False,
            },
            "run_execution_trace_usage_summary_invalid",
        ),
    ],
)
def test_run_usage_summary_rejects_inconsistent_counts(
    value: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        RunModelUsageSummary.model_validate(value)


def test_model_token_usage_is_readable_and_bound_to_response_event_hash() -> None:
    recorder = ExecutionTraceRecorder()
    recorder.record_payload(
        "model_response",
        {"status": "ok", "response": {"content": "redacted by hashing"}},
        input_tokens=321,
        output_tokens=18,
    )
    snapshot = recorder.snapshot()

    event = snapshot["events"][0]
    assert event["input_tokens"] == 321
    assert event["output_tokens"] == 18
    assert snapshot["model_usage"] == {
        "model_responses": 1,
        "usage_reports": 1,
        "input_tokens": 321,
        "output_tokens": 18,
        "complete": True,
    }
    assert ExecutionTraceSnapshot.model_validate(snapshot).trace_sha256 == snapshot["trace_sha256"]

    event["input_tokens"] = 322
    with pytest.raises(ValidationError, match="execution_trace_chain_invalid"):
        ExecutionTraceSnapshot.model_validate(snapshot)


def test_execution_trace_summarizes_partial_provider_usage_and_accepts_legacy_shape() -> None:
    recorder = ExecutionTraceRecorder()
    recorder.record_payload("model_response", {"status": "ok"}, input_tokens=70, output_tokens=8)
    recorder.record_payload("model_response", {"status": "ok"})
    snapshot = recorder.snapshot()

    assert snapshot["model_usage"] == {
        "model_responses": 2,
        "usage_reports": 1,
        "input_tokens": 70,
        "output_tokens": 8,
        "complete": False,
    }
    ExecutionTraceSnapshot.model_validate(snapshot)

    tampered_summary = {**snapshot, "model_usage": {**snapshot["model_usage"], "complete": True}}
    with pytest.raises(ValidationError, match="execution_trace_token_usage_summary_invalid"):
        ExecutionTraceSnapshot.model_validate(tampered_summary)

    legacy_shape = {key: value for key, value in snapshot.items() if key != "model_usage"}
    ExecutionTraceSnapshot.model_validate(legacy_shape)


@pytest.mark.parametrize(
    ("event_type", "input_tokens", "output_tokens"),
    [
        ("model_response", 3, None),
        ("model_request", 3, 1),
        ("model_response", -1, 1),
        ("model_response", True, 1),
    ],
)
def test_execution_trace_rejects_invalid_token_usage(
    event_type: str, input_tokens: int | None, output_tokens: int | None
) -> None:
    recorder = ExecutionTraceRecorder()
    with pytest.raises(ValueError, match="execution_trace_token_usage_invalid"):
        recorder.record_payload(
            event_type,
            {},
            input_tokens=input_tokens,
            output_tokens=output_tokens,  # type: ignore[arg-type]
        )


def test_agentscope_middleware_records_reported_model_token_usage() -> None:
    recorder = ExecutionTraceRecorder()
    middleware = ExecutionTraceMiddleware(recorder)
    response = ChatResponse(
        content=[TextBlock(text="model response")],
        is_last=True,
        usage=ChatUsage(input_tokens=77, output_tokens=12, time=0.01),
    )

    async def next_handler() -> ChatResponse:
        return response

    async def run_middleware() -> ChatResponse:
        return await middleware.on_model_call(None, {"messages": []}, next_handler)

    asyncio.run(run_middleware())
    event = recorder.snapshot()["events"][1]
    assert event["event_type"] == "model_response"
    assert event["input_tokens"] == 77
    assert event["output_tokens"] == 12
