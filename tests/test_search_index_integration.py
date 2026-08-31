"""Live OpenSearch integration coverage for strict BM25 and ACL filters."""

import asyncio
import os
import selectors
from uuid import uuid4

import httpx
import pytest

from app.schemas.knowledge import RetrievalContext
from app.services.search_index import OpenSearchBM25Service, SearchIndexRecord

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.getenv("RUN_OPENSEARCH_TESTS") != "1", reason="set RUN_OPENSEARCH_TESTS=1")
def test_live_opensearch_bm25_filters_private_chunks() -> None:
    """The live BM25 index excludes a private chunk for an unauthorized user."""

    async def run() -> None:
        index_name = f"qaagent-integration-{uuid4().hex}"
        url = os.getenv("OPENSEARCH_TEST_URL", "http://127.0.0.1:9200")
        service = OpenSearchBM25Service(url=url, index_name=index_name, verify_ssl=False)
        try:
            await service.initialize()
            await service.index_records(
                [
                    SearchIndexRecord(
                        chunk_id=1,
                        document_id=1,
                        content="deployment policy exact internal procedure",
                        title="Deployment policy",
                        source="private.md",
                        source_type="local",
                        space_slug="private",
                        is_public=False,
                        allowed_principals=("user:allowed",),
                        metadata={},
                    ),
                    SearchIndexRecord(
                        chunk_id=2,
                        document_id=2,
                        content="deployment overview for everybody",
                        title="Deployment overview",
                        source="public.md",
                        source_type="local",
                        space_slug="public",
                        is_public=True,
                        allowed_principals=(),
                        metadata={},
                    ),
                ]
            )
            async with httpx.AsyncClient() as client:
                await client.post(f"{url}/{index_name}/_refresh")

            allowed = await service.search("deployment policy", RetrievalContext(user_id="allowed"), top_k=10)
            denied = await service.search("deployment policy", RetrievalContext(user_id="denied"), top_k=10)

            assert [hit.chunk_id for hit in allowed] == [1, 2]
            assert [hit.chunk_id for hit in denied] == [2]
        finally:
            await service.close()
            async with httpx.AsyncClient() as client:
                await client.delete(f"{url}/{index_name}")

    asyncio.run(run())


@pytest.mark.skipif(
    os.getenv("RUN_OPENSEARCH_TESTS") != "1" or os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_OPENSEARCH_TESTS=1 and RUN_POSTGRES_TESTS=1",
)
def test_live_hybrid_service_uses_bm25_with_postgres_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    """KnowledgeService indexes chunks and post-validates live BM25 candidates."""
    from app.core.config import settings
    from app.schemas.knowledge import DocumentChunk
    from app.services.knowledge import KnowledgeService
    from app.services.search_index import search_index_service

    async def run() -> None:
        url = os.getenv("OPENSEARCH_TEST_URL", "http://127.0.0.1:9200")
        monkeypatch.setattr(search_index_service, "_url", url)
        service = KnowledgeService()
        suffix = uuid4().hex
        space_slug = f"bm25-{suffix}"
        source = f"bm25-{suffix}.md"

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            vectors: list[list[float]] = []
            for _ in texts:
                vector = [0.0] * settings.EMBEDDING_DIM
                vector[0] = 1.0
                vectors.append(vector)
            return vectors

        service.embed = fake_embed  # type: ignore[method-assign]
        try:
            await search_index_service.initialize()
            await service.create_space(space_slug, "Live BM25", owner_user_id="allowed")
            await service.replace_source(
                [DocumentChunk(content="zero downtime deployment policy", source=source)],
                space_slug=space_slug,
            )
            allowed = await service.search(
                "deployment policy",
                context=RetrievalContext(user_id="allowed", space_slugs=(space_slug,)),
                top_k=5,
                min_similarity=0.0,
            )
            denied = await service.search(
                "deployment policy",
                context=RetrievalContext(user_id="denied", space_slugs=(space_slug,)),
                top_k=5,
                min_similarity=0.0,
            )

            assert [hit.source for hit in allowed] == [source]
            assert "bm25" in allowed[0].retrieval_scores
            assert denied == []
        finally:
            await service.delete_space(space_slug)
            await service.close()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
