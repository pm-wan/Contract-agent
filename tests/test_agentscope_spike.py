"""Offline AgentScope 2.x tool loop smoke test with a deterministic model."""

import asyncio
from collections.abc import AsyncGenerator
from threading import Event as ThreadEvent
from typing import Any

import pytest
from pydantic import SecretStr

pytest.importorskip("agentscope")

from agentscope.agent import Agent, ReActConfig  # noqa: E402
from agentscope.credential import OpenAICredential  # noqa: E402
from agentscope.formatter import OpenAIChatFormatter  # noqa: E402
from agentscope.message import TextBlock, ToolCallBlock, UserMsg  # noqa: E402
from agentscope.model import ChatModelBase, ChatResponse  # noqa: E402
from agentscope.tool import FunctionTool, ToolChunk, Toolkit  # noqa: E402

from contract_app.agent.boundary import (
    AgentCallBoundary,
    AgentCancelled,
    AgentTimeLimit,
    ToolBudgetExceeded,
)


class DeterministicChatModel(ChatModelBase):
    def __init__(self) -> None:
        super().__init__(
            credential=OpenAICredential(api_key=SecretStr("offline-mock")),
            model="offline-mock",
            parameters=ChatModelBase.Parameters(),
            stream=False,
            max_retries=0,
        )
        self.formatter = OpenAIChatFormatter()
        self.calls = 0
        self.exposed_tool_names: list[set[str]] = []

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
        del model_name, messages, tool_choice, kwargs
        self.calls += 1
        self.exposed_tool_names.append(
            {item["function"]["name"] for item in tools or [] if "function" in item}
        )
        if self.calls == 1:
            return ChatResponse(
                content=[
                    ToolCallBlock(id="call-1", name="locate_clause", input='{"query":"付款"}')
                ],
                is_last=True,
            )
        return ChatResponse(content=[TextBlock(text="候选条款，需应用校验证据")], is_last=True)


def test_agent_invokes_only_registered_tool() -> None:
    called: list[str] = []

    def locate_clause(query: str) -> ToolChunk:
        """Locate a clause within the already authorized document snapshot."""
        called.append(query)
        return ToolChunk(content=[TextBlock(text="第 1 页付款条款")])

    model = DeterministicChatModel()
    agent = Agent(
        name="payment-candidate",
        system_prompt=(
            "Only propose candidate payment clauses. The application verifies all evidence."
        ),
        model=model,
        toolkit=Toolkit(tools=[FunctionTool(locate_clause, is_read_only=True)]),
        react_config=ReActConfig(max_iters=5),
    )
    result = asyncio.run(agent.reply(UserMsg(name="reviewer", content="定位付款约定")))
    assert model.calls >= 2
    assert called == ["付款"]
    assert "locate_clause" in model.exposed_tool_names[0]
    assert "dangerous_read" not in model.exposed_tool_names[0]
    assert any(isinstance(block, TextBlock) for block in result.content)


class SlowChatModel(DeterministicChatModel):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_observed = asyncio.Event()

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
        del model_name, messages, tools, tool_choice, kwargs
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            self.cancel_observed.set()
            raise
        return ChatResponse(content=[TextBlock(text="late")], is_last=True)


class RepeatedToolChatModel(DeterministicChatModel):
    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
        del model_name, messages, tools, tool_choice, kwargs
        self.calls += 1
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id=f"call-{self.calls}", name="locate_clause", input='{"query":"付款"}'
                )
            ],
            is_last=True,
        )


def _agent(model: ChatModelBase, tool: Any, *, max_iters: int = 8) -> Agent:
    return Agent(
        name="bounded-payment-candidate",
        system_prompt="Only propose candidate clauses; the application validates evidence.",
        model=model,
        toolkit=Toolkit(tools=[FunctionTool(tool, is_read_only=True)]),
        react_config=ReActConfig(max_iters=max_iters, interruption_raise_cancelled_error=True),
    )


def test_agent_deadline_cancels_slow_model_call() -> None:
    model = SlowChatModel()
    agent = _agent(model, lambda: ToolChunk(content=[]))
    boundary = AgentCallBoundary(timeout_seconds=0.02, max_tool_calls=2)
    with pytest.raises(AgentTimeLimit):
        asyncio.run(boundary.run(agent, UserMsg(name="reviewer", content="review")))
    assert model.cancel_observed.is_set()


def test_agent_external_cancellation_reaches_model_call() -> None:
    model = SlowChatModel()
    agent = _agent(model, lambda: ToolChunk(content=[]))
    boundary = AgentCallBoundary(timeout_seconds=2, max_tool_calls=2)
    cancellation = asyncio.Event()

    async def run_cancelled() -> None:
        task = asyncio.create_task(
            boundary.run(
                agent,
                UserMsg(name="reviewer", content="review"),
                cancellation=cancellation,
            )
        )
        await asyncio.sleep(0.02)
        cancellation.set()
        with pytest.raises(AgentCancelled):
            await task

    asyncio.run(run_cancelled())
    assert model.cancel_observed.is_set()


def test_worker_thread_cancellation_reaches_model_call() -> None:
    model = SlowChatModel()
    agent = _agent(model, lambda: ToolChunk(content=[]))
    boundary = AgentCallBoundary(timeout_seconds=2, max_tool_calls=2)
    cancellation = ThreadEvent()

    async def run_cancelled() -> None:
        task = asyncio.create_task(
            boundary.run(
                agent,
                UserMsg(name="reviewer", content="review"),
                cancellation=cancellation,
            )
        )
        await asyncio.sleep(0.02)
        cancellation.set()
        with pytest.raises(AgentCancelled):
            await task

    asyncio.run(run_cancelled())
    assert model.cancel_observed.is_set()


def test_agent_tool_budget_stops_repeated_calls_before_gateway() -> None:
    model = RepeatedToolChatModel()
    tool_queries: list[str] = []
    boundary = AgentCallBoundary(timeout_seconds=2, max_tool_calls=2)

    def locate_clause(query: str) -> ToolChunk:
        boundary.before_tool()
        tool_queries.append(query)
        return ToolChunk(content=[TextBlock(text="candidate")])

    agent = _agent(model, locate_clause)
    with pytest.raises(ToolBudgetExceeded):
        asyncio.run(boundary.run(agent, UserMsg(name="reviewer", content="review")))
    assert tool_queries == ["付款", "付款"]
    assert boundary.tool_calls == 2
