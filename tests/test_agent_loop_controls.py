"""Tests for per-turn Agent tool budgets and loop termination guards."""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

import app.core.langgraph.graph as graph_module
from app.core.config import settings
from app.core.langgraph.graph import LangGraphAgent, _final_answer_reason
from app.schemas.graph import GraphState


def _tool_call(call_id: str, query: str) -> dict:
    """Build one valid LangChain tool-call payload."""
    return {
        "name": "knowledge_search",
        "args": {"query": query},
        "id": call_id,
        "type": "tool_call",
    }


class RecordingToolExecutor:
    """Record actual calls and return deterministic tool messages."""

    def __init__(self) -> None:
        """Start with no recorded executions."""
        self.calls: list[dict] = []

    async def execute_many(self, tool_calls, tools_by_name, config):
        """Return one result for every call selected by the Agent guard."""
        self.calls.extend(tool_calls)
        return [ToolMessage(content="result", name=call["name"], tool_call_id=call["id"]) for call in tool_calls]


def test_successful_knowledge_evidence_forces_final_answer() -> None:
    """A retrieved document terminates tool use for the current user turn."""
    messages = [
        HumanMessage(content="question"),
        ToolMessage(
            content='<evidence><doc id="1" source="doc.md">answer</doc></evidence>',
            name="knowledge_search",
            tool_call_id="call-1",
        ),
    ]

    assert _final_answer_reason(messages) == "knowledge_evidence_available"


def test_duplicate_tool_call_executes_only_once() -> None:
    """Identical name/args calls in one batch produce one execution and one guard result."""
    executor = RecordingToolExecutor()
    agent = LangGraphAgent()
    agent.tool_executor = executor  # type: ignore[assignment]
    state = GraphState(
        messages=[
            HumanMessage(content="question"),
            AIMessage(content="", tool_calls=[_tool_call("call-1", "same"), _tool_call("call-2", "same")]),
        ]
    )

    command = asyncio.run(agent._tool_call(state, {}))
    assert isinstance(command.update, dict)
    outputs = command.update["messages"]

    assert [call["id"] for call in executor.calls] == ["call-1"]
    assert len(outputs) == 2
    assert outputs[1].additional_kwargs["tool_guard"] == "duplicate"
    assert _final_answer_reason([*state.messages, *outputs]) == "tool_guard_triggered"


def test_tool_budget_caps_actual_executions(monkeypatch) -> None:
    """Only calls within the configured per-turn budget reach the executor."""
    monkeypatch.setattr(settings, "AGENT_TOOL_CALL_BUDGET", 2)
    executor = RecordingToolExecutor()
    agent = LangGraphAgent()
    agent.tool_executor = executor  # type: ignore[assignment]
    state = GraphState(
        messages=[
            HumanMessage(content="question"),
            AIMessage(content="", tool_calls=[_tool_call("call-1", "first")]),
            ToolMessage(content="no evidence", name="knowledge_search", tool_call_id="call-1"),
            AIMessage(
                content="",
                tool_calls=[_tool_call("call-2", "second"), _tool_call("call-3", "third")],
            ),
        ]
    )

    command = asyncio.run(agent._tool_call(state, {}))
    assert isinstance(command.update, dict)
    outputs = command.update["messages"]

    assert [call["id"] for call in executor.calls] == ["call-2"]
    assert outputs[1].additional_kwargs["tool_guard"] == "budget_exhausted"
    assert _final_answer_reason([*state.messages, *outputs]) == "tool_guard_triggered"


def test_chat_uses_unbound_model_after_successful_knowledge_result(monkeypatch) -> None:
    """The node following successful retrieval must generate an answer without tools."""

    class RecordingLLMService:
        instances = []

        def __init__(self, runtime) -> None:
            self.bound = False
            self.messages = []
            self.instances.append(self)

        def bind_tools(self, bound_tools):
            self.bound = True
            return self

        async def call(self, messages, **kwargs):
            self.messages = messages
            return AIMessage(content="final answer")

    async def fake_runtime(user_id: int):
        return SimpleNamespace(
            model="deepseek-v4-flash",
            thinking_enabled=False,
            temperature=0.2,
        )

    monkeypatch.setattr(graph_module, "LLMService", RecordingLLMService)
    monkeypatch.setattr(graph_module.user_llm_settings_service, "get_runtime", fake_runtime)
    agent = LangGraphAgent()
    large_evidence = "证据" * 3000
    state = GraphState(
        messages=[
            HumanMessage(content="question"),
            AIMessage(content="", tool_calls=[_tool_call("call-1", "question")]),
            ToolMessage(
                content=f'<evidence><doc id="1" source="doc.md">{large_evidence}</doc></evidence>',
                name="knowledge_search",
                tool_call_id="call-1",
            ),
        ]
    )

    command = asyncio.run(agent._chat(state, {"metadata": {"user_id": 3}}))
    service = RecordingLLMService.instances[0]

    assert service.bound is False
    assert command.goto == END
    assert "Current turn tool boundary" in service.messages[0]["content"]
    assert any(message.get("content") == "question" for message in service.messages[1:])
    assert any("<evidence" in message.get("content", "") for message in service.messages[1:])
