"""PostgreSQL integration test for the pgvector knowledge path."""

import asyncio
import os
import selectors
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.langgraph.tools.knowledge_search import knowledge_search
from app.schemas.knowledge import DocumentChunk
from app.schemas.knowledge import RetrievalContext
from app.schemas.retrieval import GraphEntity, GraphExtraction, GraphRelation
from app.schemas.retrieval import QueryPlan
from app.repositories.knowledge_sync import KnowledgeSyncRepository
from app.services.database import database_service
from app.services.knowledge import (
    KnowledgeService,
    knowledge_service,
)
from app.services.knowledge_sync import KnowledgeSyncService
from app.services.knowledge_graph import KnowledgeGraphService
from app.services.query_planner import query_planner_service
from app.services.knowledge_access import knowledge_access_service
from app.services.external_acl import ExternalACLService
from app.services.connectors.base import ExternalGroupMembership, ExternalPrincipalRef
from app.models.user import User
from app.services.user_llm_settings import user_llm_settings_service


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
                    context=RetrievalContext(user_id="integration", organization_ids=(1,)),
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

        async def fake_runtime(user_id: int) -> object:
            return object()

        async def fake_plan(query, context, *, runtime, requested_intent):
            return QueryPlan(intent="qa", queries=[query], use_graph=False)

        async def fake_access(user_id: int, *, requested_spaces=()):
            return RetrievalContext(user_id=str(user_id), organization_ids=(1,))

        monkeypatch.setattr(knowledge_service, "embed", fake_embed)
        monkeypatch.setattr(user_llm_settings_service, "get_runtime", fake_runtime)
        monkeypatch.setattr(query_planner_service, "plan", fake_plan)
        monkeypatch.setattr(knowledge_access_service, "context_for_user", fake_access)
        try:
            await asyncio.wait_for(
                knowledge_service.replace_source([DocumentChunk(content="alpha tool-visible fact", source=source)]),
                timeout=10,
            )
            result = await asyncio.wait_for(
                knowledge_search.ainvoke(
                    {"query": "alpha question", "top_k": 5},
                    config={"metadata": {"user_id": "1"}},
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
                context=RetrievalContext(user_id="allowed-user", organization_ids=(1,), space_slugs=(space_slug,)),
                top_k=10,
                min_similarity=0.0,
            )
            denied_hits = await service.search(
                "alpha restricted fact",
                context=RetrievalContext(user_id="different-user", organization_ids=(1,), space_slugs=(space_slug,)),
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
                context=RetrievalContext(user_id="sync-user", organization_ids=(1,), space_slugs=(space_slug,)),
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
                context=RetrievalContext(user_id="graph-user", organization_ids=(1,), space_slugs=(space_slug,)),
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
                RetrievalContext(user_id="graph-user", organization_ids=(1,), space_slugs=(space_slug,)),
                top_k=5,
                max_hops=2,
            )
            denied = await graph.search(
                ["Deployment Service"],
                RetrievalContext(user_id="other-user", organization_ids=(1,), space_slugs=(space_slug,)),
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


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_external_group_acl_and_organization_context_prevent_cross_user_access(tmp_path) -> None:
    """External group snapshots map through the organization before retrieval."""

    async def run() -> None:
        service = KnowledgeService()
        repository = KnowledgeSyncRepository(database_service.session_factory)
        external_acl = ExternalACLService()
        suffix = uuid4().hex
        allowed_email = f"allowed-{suffix}@example.com"
        denied_email = f"denied-{suffix}@example.com"
        space_slug = f"external-acl-{suffix}"
        source = "private-policy.md"

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [_embedding(text) for text in texts]

        service.embed = fake_embed  # type: ignore[method-assign]
        allowed_user = await database_service.create_user(allowed_email, User.hash_password("Test123!"))
        denied_user = await database_service.create_user(denied_email, User.hash_password("Test123!"))
        try:
            await service.create_space(space_slug, "External ACL", organization_id=1)
            connector_id = await repository.create_local_connector(space_slug, "external-acl", str(tmp_path))
            await service.replace_source(
                [DocumentChunk(content="private deployment approval", source=source)],
                space_slug=space_slug,
                source_type="local",
                connector_id=connector_id,
            )
            await external_acl.sync_group_memberships(
                connector_id,
                (ExternalGroupMembership("engineering", "Engineering", (allowed_email,)),),
            )
            inserted = await external_acl.apply_document_acl(
                connector_id,
                space_slug=space_slug,
                source_type="local",
                external_id=source,
                principals=(ExternalPrincipalRef("group", "engineering", "Engineering"),),
            )
            allowed_context = await knowledge_access_service.context_for_user(
                allowed_user.id,
                requested_spaces=[space_slug],
            )
            denied_context = await knowledge_access_service.context_for_user(
                denied_user.id,
                requested_spaces=[space_slug],
            )
            allowed_hits = await service.search(
                "deployment approval",
                context=allowed_context,
                top_k=5,
                min_similarity=0.0,
            )
            denied_hits = await service.search(
                "deployment approval",
                context=denied_context,
                top_k=5,
                min_similarity=0.0,
            )

            assert inserted == 1
            assert [hit.source for hit in allowed_hits] == [source]
            assert denied_hits == []
            assert allowed_context.organization_ids == (1,)
            assert len(allowed_context.group_ids) == 1
        finally:
            await service.delete_space(space_slug)
            await database_service.delete_user_by_email(allowed_email)
            await database_service.delete_user_by_email(denied_email)
            await service.close()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_public_space_is_still_isolated_by_organization() -> None:
    """A public flag never makes a space visible outside organization membership."""

    async def run() -> None:
        service = KnowledgeService()
        suffix = uuid4().hex
        organization_slug = f"tenant-{suffix}"
        other_email = f"tenant-user-{suffix}@example.com"
        space_slug = f"org-public-{suffix}"
        source = "organization-policy.md"

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [_embedding(text) for text in texts]

        service.embed = fake_embed  # type: ignore[method-assign]
        other_user = await database_service.create_user(other_email, User.hash_password("Test123!"))
        organization_id: int | None = None
        try:
            async with database_service.session_factory() as session, session.begin():
                organization_id = int(
                    (
                        await session.exec(
                            text("INSERT INTO organizations (slug, name) VALUES (:slug, :name) RETURNING id"),
                            params={"slug": organization_slug, "name": "Other tenant"},
                        )
                    ).scalar_one()
                )
                await session.exec(
                    text("DELETE FROM organization_members WHERE user_id = :user_id"),
                    params={"user_id": other_user.id},
                )
                await session.exec(
                    text(
                        """
                        INSERT INTO organization_members (organization_id, user_id, role)
                        VALUES (:organization_id, :user_id, 'member')
                        """
                    ),
                    params={"organization_id": organization_id, "user_id": other_user.id},
                )
            await service.create_space(space_slug, "Organization public", is_public=True, organization_id=1)
            await service.replace_source(
                [DocumentChunk(content="organization one public policy", source=source)],
                space_slug=space_slug,
            )
            other_context = await knowledge_access_service.context_for_user(other_user.id)
            hits = await service.search(
                "organization public policy",
                context=other_context,
                top_k=5,
                min_similarity=0.0,
            )

            assert other_context.organization_ids == (organization_id,)
            assert hits == []
        finally:
            await service.delete_space(space_slug)
            await database_service.delete_user_by_email(other_email)
            if organization_id is not None:
                async with database_service.session_factory() as session, session.begin():
                    await session.exec(
                        text("DELETE FROM organizations WHERE id = :organization_id"),
                        params={"organization_id": organization_id},
                    )
            await service.close()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
