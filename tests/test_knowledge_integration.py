"""PostgreSQL integration test for the pgvector knowledge path."""

import asyncio
import os
import selectors
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.langgraph.tools.knowledge_search import knowledge_search
from app.schemas.knowledge import DocumentChunk
from app.services.knowledge import (
    KnowledgeService,
    knowledge_service,
)


pytestmark = pytest.mark.integration


def _embedding(text: str) -> list[float]:
    vector = [0.0] * settings.EMBEDDING_DIM
    vector[0 if "alpha" in text.lower() else 1] = 1.0
    return vector


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_replace_and_search_source_in_pgvector() -> None:
    """Replacing a source removes stale chunks and keeps retrieval working."""

    async def run() -> None:
        service = KnowledgeService()
        source = f"integration-{uuid4()}.txt"

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [_embedding(text) for text in texts]

        service.embed = fake_embed  # type: ignore[method-assign]
        try:
            inserted = await asyncio.wait_for(
                service.replace_source(
                    [
                        DocumentChunk(content="alpha original fact", source=source),
                        DocumentChunk(content="beta stale fact", source=source),
                    ]
                ),
                timeout=10,
            )
            assert inserted == 2

            inserted = await asyncio.wait_for(
                service.replace_source([DocumentChunk(content="alpha replacement fact", source=source)]),
                timeout=10,
            )
            assert inserted == 1

            hits = await asyncio.wait_for(
                service.search("alpha question", top_k=5, min_similarity=0.0),
                timeout=10,
            )
            source_hits = [hit for hit in hits if hit.source == source]
            assert [hit.content for hit in source_hits] == ["alpha replacement fact"]
        finally:
            await asyncio.wait_for(service.delete_source(source), timeout=10)
            await asyncio.wait_for(service.close(), timeout=10)

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_knowledge_search_tool_returns_pgvector_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """The LangChain tool should expose stored pgvector evidence to the agent."""

    async def run() -> None:
        source = f"tool-integration-{uuid4()}.txt"

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [_embedding(text) for text in texts]

        monkeypatch.setattr(knowledge_service, "embed", fake_embed)
        try:
            await asyncio.wait_for(
                knowledge_service.replace_source([DocumentChunk(content="alpha tool-visible fact", source=source)]),
                timeout=10,
            )
            result = await asyncio.wait_for(
                knowledge_search.ainvoke({"query": "alpha question", "top_k": 5}),
                timeout=10,
            )
            assert source in result
            assert "alpha tool-visible fact" in result
        finally:
            await asyncio.wait_for(knowledge_service.delete_source(source), timeout=10)
            await asyncio.wait_for(knowledge_service.close(), timeout=10)

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
