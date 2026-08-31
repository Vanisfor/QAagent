"""Tests for server-injected RAG access context."""

import asyncio
from typing import Any

from app.core.langgraph.tools.knowledge_search import (
    knowledge_search,
    retrieval_pipeline,
    user_llm_settings_service,
)
from app.schemas.knowledge import KnowledgeHit, RetrievalContext
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


def test_knowledge_search_passes_injected_access_context(monkeypatch) -> None:
    """The model-visible tool schema cannot choose the authenticated principal."""
    captured: dict[str, Any] = {}

    async def fake_runtime(user_id: int) -> object:
        captured["runtime_user_id"] = user_id
        return object()

    async def fake_retrieve(query, context, runtime, *, intent, top_k):
        captured.update(query=query, context=context, top_k=top_k, intent=intent)
        return RetrievalBundle(
            plan=QueryPlan(intent="qa", queries=[query]),
            hits=[KnowledgeHit(chunk_id=1, document_id=2, content="allowed", source="policy.md", score=0.9)],
        )

    monkeypatch.setattr(user_llm_settings_service, "get_runtime", fake_runtime)
    monkeypatch.setattr(retrieval_pipeline, "retrieve", fake_retrieve)

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
    assert "config" not in knowledge_search.args
