"""Policy-aware execution for LangGraph tools."""

import asyncio
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools.base import BaseTool
from langgraph.errors import GraphInterrupt
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.langgraph.tool_policy import ToolIdempotency, get_tool_policy
from app.core.logging import logger
from app.core.tracing import trace_span


class ToolExecutor:
    """Execute tools concurrently while respecting per-tool safety policies."""

    async def execute_many(
        self,
        tool_calls: list[dict[str, Any]],
        tools_by_name: Mapping[str, BaseTool],
    ) -> list[ToolMessage]:
        """Execute all requested tools and preserve their call order."""
        if len(tool_calls) == 1:
            return [await self.execute(tool_calls[0], tools_by_name)]
        return list(await asyncio.gather(*(self.execute(call, tools_by_name) for call in tool_calls)))

    async def execute(
        self,
        tool_call: dict[str, Any],
        tools_by_name: Mapping[str, BaseTool],
    ) -> ToolMessage:
        """Execute one tool with bounded time and safe retries."""
        tool_name = str(tool_call["name"])
        tool = tools_by_name.get(tool_name)
        if tool is None:
            logger.warning("unknown_tool_requested", tool_name=tool_name)
            return ToolMessage(
                content=f"Tool '{tool_name}' is unavailable.",
                name=tool_name,
                tool_call_id=str(tool_call["id"]),
            )

        policy = get_tool_policy(tool_name)
        attempts = policy.max_attempts if policy.idempotency != ToolIdempotency.NON_IDEMPOTENT else 1
        result: Any = ""
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
                retry=retry_if_exception_type(Exception),
                reraise=True,
            ):
                with attempt:
                    with trace_span(
                        "tool.execute",
                        tool_name=tool_name,
                        tool_idempotency=policy.idempotency.value,
                    ) as span:
                        span.set_attribute("attempt", attempt.retry_state.attempt_number)
                        if policy.timeout_seconds is None:
                            result = await tool.ainvoke(tool_call["args"])
                        else:
                            async with asyncio.timeout(policy.timeout_seconds):
                                result = await tool.ainvoke(tool_call["args"])
        except GraphInterrupt:
            raise
        except Exception as error:
            logger.exception(
                "tool_execution_failed",
                tool_name=tool_name,
                idempotency=policy.idempotency.value,
                max_attempts=attempts,
                error_type=type(error).__name__,
            )
            result = f"Tool '{tool_name}' failed after {attempts} attempt(s)."

        return ToolMessage(
            content=str(result),
            name=tool_name,
            tool_call_id=str(tool_call["id"]),
        )
