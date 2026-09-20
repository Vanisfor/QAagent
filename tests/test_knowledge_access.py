"""Tests for server-injected RAG access context."""

import asyncio
from typing import Any

from app.schemas.chat import ChatRequest
from app.core.langgraph.tools.knowledge_search import (
    agentic_rag_workflow,
    knowledge_search,
    knowledge_access_service,
    user_llm_settings_service,
)
from app.schemas.knowledge import KnowledgeHit, RetrievalContext
from app.services.knowledge import KnowledgeService
from app.schemas.retrieval import QueryPlan, RetrievalBundle


def test_retrieval_context_uses_server_metadata() -> None:
    """Authenticated identity and groups are read from RunnableConfig metadata."""
    context = RetrievalContext.from_config(
        {
            "metadata": {
                "user_id": "7",
                "knowledge_group_ids": ["engineering", "security"],
                "knowledge_space_slugs": ["product"],
            }
        }
    )

    assert context.user_id == "7"
    assert context.principals == (("user", "7"), ("group", "engineering"), ("group", "security"))
    assert context.space_slugs == ("product",)
    assert context.space_scope_requested is True


def test_chat_request_normalizes_requested_space_slugs() -> None:
    """The client may request bounded spaces but cannot submit authorization data."""
    request = ChatRequest(
        messages=[{"role": "user", "content": "question"}],
        space_slugs=[" private ", "private", "team"],
    )

    assert request.space_slugs == ["private", "team"]


def test_knowledge_search_passes_injected_access_context(monkeypatch) -> None:
    """The model-visible tool schema cannot choose the authenticated principal."""
    captured: dict[str, Any] = {}

    async def fake_runtime(user_id: int) -> object:
        captured["runtime_user_id"] = user_id
        return object()

    async def fake_access(user_id: int, *, requested_spaces=()) -> RetrievalContext:
        return RetrievalContext(
            user_id=str(user_id),
            organization_ids=(1,),
            group_ids=("legal",),
            space_slugs=tuple(requested_spaces),
        )

    async def fake_run(query, context, runtime, *, intent, top_k, config=None):
        captured.update(query=query, context=context, top_k=top_k, intent=intent, config=config)
        return RetrievalBundle(
            plan=QueryPlan(intent="qa", queries=[query]),
            hits=[KnowledgeHit(chunk_id=1, document_id=2, content="allowed", source="policy.md", score=0.9)],
        )

    monkeypatch.setattr(user_llm_settings_service, "get_runtime", fake_runtime)
    monkeypatch.setattr(knowledge_access_service, "context_for_user", fake_access)
    monkeypatch.setattr(agentic_rag_workflow, "run", fake_run)

    result = asyncio.run(
        knowledge_search.ainvoke(
            {"query": "policy", "top_k": 3},
            config={"metadata": {"user_id": "42", "knowledge_group_ids": ["legal"]}},
        )
    )

    assert "allowed" in result
    assert captured["context"].principals == (("user", "42"), ("group", "legal"))
    assert captured["top_k"] == 3
    assert captured["runtime_user_id"] == 42
    assert captured["intent"] == "qa"
    assert captured["config"]["metadata"]["user_id"] == "42"
    assert "config" not in knowledge_search.args


def test_knowledge_search_does_not_fall_back_when_requested_spaces_are_denied(monkeypatch) -> None:
    """An unauthorized explicit scope must not become an unscoped full search."""
    called = False

    async def fake_access(user_id: int, *, requested_spaces=()) -> RetrievalContext:
        return RetrievalContext(
            user_id=str(user_id),
            organization_ids=(1,),
            space_slugs=(),
            space_scope_requested=True,
        )

    async def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("RAG must not run for a denied explicit space scope")

    monkeypatch.setattr(knowledge_access_service, "context_for_user", fake_access)
    monkeypatch.setattr(agentic_rag_workflow, "run", fake_run)

    result = asyncio.run(
        knowledge_search.ainvoke(
            {"query": "private policy"},
            config={"metadata": {"user_id": "42", "knowledge_space_slugs": ["denied"]}},
        )
    )

    assert called is False
    assert "No accessible internal evidence" in result


def test_direct_knowledge_search_rejects_denied_explicit_scope(monkeypatch) -> None:
    """Direct service callers must not search all spaces after authorization returned none."""
    service = KnowledgeService()

    async def forbidden_embedding(_texts):
        raise AssertionError("denied scope must stop before embedding")

    monkeypatch.setattr(service, "embed", forbidden_embedding)
    result = asyncio.run(service.search(
        "private policy",
        context=RetrievalContext(user_id="7", organization_ids=(1,), space_scope_requested=True),
    ))
    assert result == []
