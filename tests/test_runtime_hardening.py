"""Regression tests for runtime lifecycle and low-cardinality policies."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, Request
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt

from app.api.v1.auth import db_service
from app.api.v1.chatbot import _acquire_session_run, session_run_lock_service
from app.core.config import settings
from app.core.langgraph.tool_executor import ToolExecutor
from app.core.langgraph.tool_policy import TOOL_POLICIES, ToolIdempotency, ToolPolicy, get_tool_policy
from app.core.middleware import route_template
from app.main import _readiness_response, agent, liveness_check
from app.services.database import database_service
from app.services.memory_jobs import MemoryJobService
from app.repositories.memory_jobs import ClaimedMemoryJob
from app.services.memory_jobs import MemoryJobLeaseLost
from app.services.memory import memory_service
from app.services.session_runs import SessionRunLockService


def test_auth_uses_shared_database_service() -> None:
    """Routes must not create a second SQLAlchemy engine and pool."""
    assert db_service is database_service


def test_metrics_use_route_template_not_dynamic_path() -> None:
    """Path parameters must not become Prometheus label values."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/session/abc",
            "route": SimpleNamespace(path="/session/{session_id}"),
        }
    )
    assert route_template(request) == "/session/{session_id}"

    unmatched = Request({"type": "http", "method": "GET", "path": "/random-value"})
    assert route_template(unmatched) == "__unmatched__"


def test_registered_tools_have_explicit_safety_classification() -> None:
    """Every production tool has an intentional idempotency and retry policy."""
    assert get_tool_policy("knowledge_search").idempotency == ToolIdempotency.READ_ONLY
    assert get_tool_policy("duckduckgo_results_json").max_attempts == 2
    assert get_tool_policy("ask_human") == ToolPolicy(None, 1, ToolIdempotency.NON_IDEMPOTENT)


def test_read_only_tool_retries_but_non_idempotent_tool_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries are bounded by idempotency rather than the whole graph node."""
    calls = 0

    @tool("flaky")
    async def flaky() -> str:
        """Fail once, then succeed."""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return "ok"

    monkeypatch.setitem(TOOL_POLICIES, "flaky", ToolPolicy(1.0, 2, ToolIdempotency.READ_ONLY))
    message = asyncio.run(
        ToolExecutor().execute(
            {"name": "flaky", "id": "call-1", "args": {}},
            {"flaky": flaky},
        )
    )
    assert message.content == "ok"
    assert calls == 2

    calls = 0
    monkeypatch.setitem(TOOL_POLICIES, "flaky", ToolPolicy(1.0, 5, ToolIdempotency.NON_IDEMPOTENT))
    message = asyncio.run(
        ToolExecutor().execute(
            {"name": "flaky", "id": "call-2", "args": {}},
            {"flaky": flaky},
        )
    )
    assert "failed after 1 attempt" in str(message.content)
    assert calls == 1


def test_tool_executor_preserves_graph_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Human-in-the-loop interrupts must reach LangGraph unchanged."""

    @tool("interrupting")
    async def interrupting() -> str:
        """Pause graph execution."""
        raise GraphInterrupt()

    monkeypatch.setitem(TOOL_POLICIES, "interrupting", ToolPolicy(None, 1, ToolIdempotency.NON_IDEMPOTENT))
    with pytest.raises(GraphInterrupt):
        asyncio.run(
            ToolExecutor().execute(
                {"name": "interrupting", "id": "call-interrupt", "args": {}},
                {"interrupting": interrupting},
            )
        )


def test_live_and_ready_have_distinct_dependency_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness stays up while readiness rejects unavailable dependencies."""

    async def database_down() -> bool:
        return False

    monkeypatch.setattr(database_service, "health_check", database_down)
    monkeypatch.setattr(type(agent), "is_ready", property(lambda self: True))

    live = asyncio.run(liveness_check())
    ready = asyncio.run(_readiness_response())
    assert live.status_code == 200
    assert ready.status_code == 503


def test_memory_job_idempotency_key_is_stable() -> None:
    """Re-enqueuing the same completed exchange must use the same outbox key."""
    service = MemoryJobService()
    keys: list[str] = []

    class Repository:
        async def enqueue(self, **values: Any) -> bool:
            keys.append(str(values["idempotency_key"]))
            return True

    service._repository = Repository()  # type: ignore[assignment]
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    asyncio.run(service.enqueue("7", messages, {"session_id": "session-1"}))
    asyncio.run(service.enqueue("7", messages, {"session_id": "session-1"}))
    assert len(keys) == 2
    assert keys[0] == keys[1]


def test_session_run_lock_rejects_an_existing_owner() -> None:
    """A failed PostgreSQL advisory-lock attempt must close its connection."""

    class Result:
        def scalar_one(self) -> bool:
            return False

    class Session:
        def __init__(self) -> None:
            self.closed = False

        async def exec(self, statement: Any, *, params: dict[str, Any]) -> Result:
            return Result()

        async def close(self) -> None:
            self.closed = True

    session = Session()
    service = SessionRunLockService(lambda: session)  # type: ignore[arg-type]

    assert asyncio.run(service.try_acquire("session-1")) is None
    assert session.closed is True


def test_overlapping_session_run_returns_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API contract rejects a second run with HTTP 409 instead of queuing."""

    async def deny_lock(session_id: str) -> None:
        return None

    monkeypatch.setattr(session_run_lock_service, "try_acquire", deny_lock)

    with pytest.raises(HTTPException) as error:
        asyncio.run(_acquire_session_run("session-1"))

    assert error.value.status_code == 409


def test_memory_job_renews_lease_during_long_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long mem0 writes heartbeat before their stale-claim window."""
    service = MemoryJobService()
    renewals = 0

    class Repository:
        async def renew(self, job_id: int, lease_token: str) -> bool:
            nonlocal renewals
            renewals += 1
            return True

    async def slow_add(user_id: str, messages: list[dict], metadata: dict) -> None:
        await asyncio.sleep(0.12)

    service._repository = Repository()  # type: ignore[assignment]
    monkeypatch.setattr(memory_service, "add", slow_add)
    monkeypatch.setattr(settings, "MEMORY_JOB_HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setattr(settings, "MEMORY_JOB_STALE_AFTER_SECONDS", 1)
    job = ClaimedMemoryJob(1, "7", [], {}, 1, "owner-token")

    asyncio.run(service._process_with_heartbeat(job))

    assert renewals >= 1


def test_memory_job_cancels_processing_after_lease_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    """An old worker stops work instead of settling a newly claimed job."""
    service = MemoryJobService()
    cancelled = False

    class Repository:
        async def renew(self, job_id: int, lease_token: str) -> bool:
            return False

    async def blocked_add(user_id: str, messages: list[dict], metadata: dict) -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    service._repository = Repository()  # type: ignore[assignment]
    monkeypatch.setattr(memory_service, "add", blocked_add)
    monkeypatch.setattr(settings, "MEMORY_JOB_HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setattr(settings, "MEMORY_JOB_STALE_AFTER_SECONDS", 1)
    job = ClaimedMemoryJob(1, "7", [], {}, 1, "old-owner-token")

    with pytest.raises(MemoryJobLeaseLost):
        asyncio.run(service._process_with_heartbeat(job))

    assert cancelled is True
