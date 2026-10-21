"""Hash model-call contexts at the AgentScope boundary without storing content."""

from typing import Any

from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse

from contract_app.workflow.execution_trace import ExecutionTraceRecorder


class ExecutionTraceMiddleware(MiddlewareBase):
    def __init__(self, recorder: ExecutionTraceRecorder) -> None:
        self._recorder = recorder

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Any,
    ) -> ChatResponse:
        del agent
        messages = input_kwargs.get("messages")
        tools = input_kwargs.get("tools")
        tool_choice = input_kwargs.get("tool_choice")
        if not isinstance(messages, list):
            raise ValueError("execution_trace_model_request_invalid")
        self._recorder.record_payload(
            "model_request",
            {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
            },
        )
        try:
            response = await next_handler()
        except Exception as exc:
            self._recorder.record_payload(
                "model_response",
                {"status": "error", "error_type": type(exc).__name__},
            )
            raise
        if not isinstance(response, ChatResponse):
            # Streaming is disabled in the frozen execution profile. Refuse an
            # untraced response if a provider or framework violates that contract.
            raise ValueError("execution_trace_streaming_response_unsupported")
        usage = response.usage
        self._recorder.record_payload(
            "model_response",
            {"status": "ok", "response": response},
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
        )
        return response
