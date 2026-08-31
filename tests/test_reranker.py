"""Tests for Cross-Encoder reranking and safe fallback."""

import asyncio

import httpx

from app.schemas.knowledge import KnowledgeHit
from app.services.reranker import RerankerService


def _hits() -> list[KnowledgeHit]:
    return [
        KnowledgeHit(chunk_id=1, document_id=1, content="generic", source="a.md", score=0.8),
        KnowledgeHit(chunk_id=2, document_id=2, content="exact deployment policy", source="b.md", score=0.7),
    ]


def test_reranker_uses_provider_indices_and_scores() -> None:
    """Provider result indices reorder candidates without returning document text."""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rerank"
        payload = __import__("json").loads(request.content)
        assert payload["return_documents"] is False
        assert payload["documents"] == ["generic", "exact deployment policy"]
        return httpx.Response(
            200,
            json={
                "id": "rerank-test",
                "results": [
                    {"index": 1, "relevance_score": 0.95},
                    {"index": 0, "relevance_score": 0.2},
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.siliconflow.cn")
    service = RerankerService(
        api_key="test-key",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-reranker-v2-m3",
        enabled=True,
        client=client,
    )
    try:
        reranked = asyncio.run(service.rerank("deployment policy", _hits(), top_n=2))
    finally:
        asyncio.run(client.aclose())

    assert [hit.chunk_id for hit in reranked] == [2, 1]
    assert reranked[0].score == 0.95
    assert reranked[0].retrieval_scores["reranker"] == 0.95


def test_reranker_failure_falls_back_to_fused_order() -> None:
    """Provider failure cannot make the whole RAG request fail."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "unavailable"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.siliconflow.cn")
    service = RerankerService(
        api_key="test-key",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-reranker-v2-m3",
        enabled=True,
        client=client,
    )
    try:
        reranked = asyncio.run(service.rerank("deployment policy", _hits(), top_n=1))
    finally:
        asyncio.run(client.aclose())

    assert [hit.chunk_id for hit in reranked] == [1]
