"""Cooperative deadline, cancellation and read-tool call budget for AgentScope.

This does not terminate a blocking synchronous SDK call. Worker fencing and
process isolation remain necessary before using untrusted external tools.
"""

import asyncio
from time import monotonic

from agentscope.agent import Agent
from agentscope.message import Msg

from contract_app.workflow.cancellation import CancellationSignal as ThreadCancellationSignal

CancellationSignal = asyncio.Event | ThreadCancellationSignal


async def _wait_for_cancellation(cancellation: CancellationSignal) -> None:
    if isinstance(cancellation, asyncio.Event):
        await cancellation.wait()
        return
    while not cancellation.is_set():
        await asyncio.sleep(0.025)


class AgentTimeLimit(TimeoutError):
    """The model or tool loop exceeded its wall-clock deadline."""


class AgentCancelled(Exception):
    """The application cancelled this agent reply."""


class ToolBudgetExceeded(Exception):
    """The model attempted more tool calls than allowed."""


class AgentCallBoundary:
    """Single-use guard shared by one reply and its registered read-only tools."""

    def __init__(self, *, timeout_seconds: float, max_tool_calls: int) -> None:
        if timeout_seconds <= 0 or max_tool_calls <= 0:
            raise ValueError("invalid_agent_budget")
        self.timeout_seconds = timeout_seconds
        self.max_tool_calls = max_tool_calls
        self.tool_calls = 0
        self._deadline: float | None = None
        self._cancel: CancellationSignal | None = None
        self._budget_exhausted = asyncio.Event()

    def before_tool(self) -> None:
        """Call before any model-selected tool reaches an authorization gateway."""
        if self._deadline is None:
            raise RuntimeError("agent_boundary_not_running")
        if self._cancel is not None and self._cancel.is_set():
            raise AgentCancelled()
        if monotonic() >= self._deadline:
            raise AgentTimeLimit()
        if self.tool_calls >= self.max_tool_calls:
            self._budget_exhausted.set()
            raise ToolBudgetExceeded()
        self.tool_calls += 1

    async def run(
        self, agent: Agent, message: Msg, *, cancellation: CancellationSignal | None = None
    ) -> Msg:
        """Cancel a cooperative reply on timeout, explicit cancel or tool overrun."""
        if self._deadline is not None:
            raise RuntimeError("agent_boundary_single_use")
        self._deadline = monotonic() + self.timeout_seconds
        self._cancel = cancellation
        if cancellation is not None and cancellation.is_set():
            raise AgentCancelled()
        reply = asyncio.create_task(agent.reply(message))
        budget_wait = asyncio.create_task(self._budget_exhausted.wait())
        cancel_wait = (
            asyncio.create_task(_wait_for_cancellation(cancellation))
            if cancellation is not None
            else None
        )
        watchers = {reply, budget_wait}
        if cancel_wait is not None:
            watchers.add(cancel_wait)
        try:
            done, _pending = await asyncio.wait(
                watchers, timeout=self.timeout_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            if self._budget_exhausted.is_set():
                raise ToolBudgetExceeded()
            if cancellation is not None and cancellation.is_set():
                raise AgentCancelled()
            if reply in done:
                return await reply
            raise AgentTimeLimit()
        finally:
            if not reply.done():
                reply.cancel()
            budget_wait.cancel()
            if cancel_wait is not None:
                cancel_wait.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)
