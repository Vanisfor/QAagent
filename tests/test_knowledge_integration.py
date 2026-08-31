"""PostgreSQL integration test for the pgvector knowledge path."""

import asyncio
import os
import selectors
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.langgraph.tools.knowledge_search import knowledge_search
from app.schemas.knowledge import DocumentChunk
from app.schemas.knowledge import RetrievalContext
from app.schemas.retrieval import GraphEntity, GraphExtraction, GraphRelation
from app.repositories.knowledge_sync import KnowledgeSyncRepository
from app.services.database import database_service
from app.services.knowledge import (
    KnowledgeService,
    knowledge_service,
)
from app.services.knowledge_sync import KnowledgeSyncService
from app.services.knowledge_graph import KnowledgeGraphService


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
                service.search(
                    "alpha question",
                    context=RetrievalContext(user_id="integration"),
                    top_k=5,
                    min_similarity=0.0,
                ),
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
                knowledge_search.ainvoke(
                    {"query": "alpha question", "top_k": 5},
                    config={"metadata": {"user_id": "integration"}},
                ),
                timeout=10,
            )
            assert source in result
            assert "alpha tool-visible fact" in result
        finally:
            await asyncio.wait_for(knowledge_service.delete_source(source), timeout=10)
            await asyncio.wait_for(knowledge_service.close(), timeout=10)

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_private_space_is_filtered_before_hybrid_ranking() -> None:
    """Only an allowed principal can retrieve a private-space document."""

    async def run() -> None:
        service = KnowledgeService()
        suffix = uuid4().hex
        space_slug = f"private-{suffix}"
        source = f"restricted-{suffix}.txt"

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [_embedding(text) for text in texts]

        service.embed = fake_embed  # type: ignore[method-assign]
        try:
            await service.create_space(
                space_slug,
                "Private integration space",
                owner_user_id="allowed-user",
            )
            await service.replace_source(
                [DocumentChunk(content="alpha restricted fact", source=source)],
                space_slug=space_slug,
            )

            allowed_hits = await service.search(
                "alpha restricted fact",
                context=RetrievalContext(user_id="allowed-user", space_slugs=(space_slug,)),
                top_k=10,
                min_similarity=0.0,
            )
            denied_hits = await service.search(
                "alpha restricted fact",
                context=RetrievalContext(user_id="different-user", space_slugs=(space_slug,)),
                top_k=10,
                min_similarity=0.0,
            )

            assert [hit.source for hit in allowed_hits] == [source]
            assert denied_hits == []
        finally:
            await service.delete_space(space_slug)
            await service.close()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_local_connector_cursor_and_tombstone_in_postgres(tmp_path) -> None:
    """A durable local sync advances its cursor and propagates source deletion."""

    async def run() -> None:
        service = KnowledgeService()
        repository = KnowledgeSyncRepository(database_service.session_factory)
        sync_service = KnowledgeSyncService(repository, service)
        suffix = uuid4().hex
        space_slug = f"sync-{suffix}"
        source_file = tmp_path / "handbook.md"
        source_file.write_text("alpha connector policy", encoding="utf-8")

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [_embedding(text) for text in texts]

        service.embed = fake_embed  # type: ignore[method-assign]
        try:
            await service.create_space(space_slug, "Sync integration", owner_user_id="sync-user")
            connector_id = await repository.create_local_connector(space_slug, "handbook", str(tmp_path))

            first = await sync_service.sync(connector_id)
            unchanged = await sync_service.sync(connector_id)
            source_file.unlink()
            deleted = await sync_service.sync(connector_id)
            hits = await service.search(
                "alpha connector policy",
                context=RetrievalContext(user_id="sync-user", space_slugs=(space_slug,)),
                top_k=10,
                min_similarity=0.0,
            )
            connector = await repository.get_connector(connector_id)

            assert first.documents_upserted == 1
            assert first.chunks_upserted == 1
            assert unchanged.documents_seen == 0
            assert deleted.documents_deleted == 1
            assert connector.cursor["files"] == {}
            assert hits == []
        finally:
            await service.delete_space(space_slug)
            await service.close()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_knowledge_graph_traversal_preserves_acl_and_chunk_provenance() -> None:
    """Graph facts are returned only through authorized source chunks."""

    async def run() -> None:
        service = KnowledgeService()
        graph = KnowledgeGraphService()
        suffix = uuid4().hex
        space_slug = f"graph-{suffix}"
        source = f"architecture-{suffix}.md"

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [_embedding(text) for text in texts]

        service.embed = fake_embed  # type: ignore[method-assign]
        try:
            await service.create_space(space_slug, "Graph integration", owner_user_id="graph-user")
            await service.replace_source(
                [
                    DocumentChunk(
                        content="Deployment Service writes to Production DB.",
                        source=source,
                        metadata={"chunk_index": 0},
                    )
                ],
                space_slug=space_slug,
            )
            hits = await service.search(
                "Deployment Service",
                context=RetrievalContext(user_id="graph-user", space_slugs=(space_slug,)),
                top_k=5,
                min_similarity=0.0,
            )
            document_id = hits[0].document_id
            await graph.replace_document_graph(
                document_id,
                GraphExtraction(
                    entities=[
                        GraphEntity(key="service", name="Deployment Service", entity_type="system"),
                        GraphEntity(key="database", name="Production DB", entity_type="database"),
                    ],
                    relations=[
                        GraphRelation(
                            source_key="service",
                            target_key="database",
                            predicate="writes_to",
                            chunk_index=0,
                        )
                    ],
                ),
            )

            allowed = await graph.search(
                ["Deployment Service"],
                RetrievalContext(user_id="graph-user", space_slugs=(space_slug,)),
                top_k=5,
                max_hops=2,
            )
            denied = await graph.search(
                ["Deployment Service"],
                RetrievalContext(user_id="other-user", space_slugs=(space_slug,)),
                top_k=5,
                max_hops=2,
            )

            assert [hit.chunk_id for hit in allowed] == [hits[0].chunk_id]
            assert allowed[0].metadata["graph_relation"]["predicate"] == "writes_to"
            assert denied == []
        finally:
            await service.delete_space(space_slug)
            await service.close()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
