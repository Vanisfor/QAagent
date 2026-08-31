"""Tests for the OpenSearch BM25 adapter."""

import asyncio
import json

import httpx

from app.schemas.knowledge import RetrievalContext
from app.services.search_index import OpenSearchBM25Service, SearchIndexRecord


def test_opensearch_search_applies_acl_and_space_filters() -> None:
    """BM25 candidates must be filtered by authenticated principals in OpenSearch."""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {"_id": "11", "_score": 4.2},
                        {"_id": "7", "_score": 3.1},
                    ]
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://search:9200")
    service = OpenSearchBM25Service(url="http://search:9200", index_name="qa-knowledge", client=client)
    try:
        hits = asyncio.run(
            service.search(
                "deployment policy",
                RetrievalContext(user_id="42", group_ids=("engineering",), space_slugs=("product",)),
                top_k=10,
            )
        )
    finally:
        asyncio.run(client.aclose())

    body = json.loads(requests[0].content)
    filters = body["query"]["bool"]["filter"]
    acl_filter = filters[0]["bool"]
    assert {"term": {"is_public": True}} in acl_filter["should"]
    assert {"terms": {"allowed_principals": ["user:42", "group:engineering"]}} in acl_filter["should"]
    assert {"terms": {"space_slug": ["product"]}} in filters
    assert [(hit.chunk_id, hit.score) for hit in hits] == [(11, 4.2), (7, 3.1)]


def test_opensearch_bulk_index_uses_stable_chunk_ids() -> None:
    """Bulk indexing uses chunk IDs for idempotent upserts and stores ACL principals."""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"errors": False, "items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://search:9200")
    service = OpenSearchBM25Service(url="http://search:9200", index_name="qa-knowledge", client=client)
    record = SearchIndexRecord(
        chunk_id=9,
        document_id=3,
        content="internal deployment policy",
        title="Policy",
        source="policy.md",
        source_type="local",
        space_slug="private",
        is_public=False,
        allowed_principals=("user:7",),
        metadata={"section": "deploy"},
    )
    try:
        asyncio.run(service.index_records([record]))
    finally:
        asyncio.run(client.aclose())

    lines = requests[0].content.decode("utf-8").strip().splitlines()
    assert json.loads(lines[0]) == {"index": {"_index": "qa-knowledge", "_id": "9"}}
    assert json.loads(lines[1])["allowed_principals"] == ["user:7"]
