"""Strict BM25 indexing and retrieval through OpenSearch."""

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import RetrievalContext

_INDEX_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class SearchIndexRecord:
    """One ACL-bearing chunk document stored in the BM25 index."""

    chunk_id: int
    document_id: int
    content: str
    title: str
    source: str
    source_type: str
    space_slug: str
    is_public: bool
    allowed_principals: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SearchIndexHit:
    """Content-free BM25 candidate returned for PostgreSQL hydration."""

    chunk_id: int
    score: float


class OpenSearchBM25Service:
    """Minimal async OpenSearch client with BM25 and ACL-aware queries."""

    def __init__(
        self,
        *,
        url: str,
        index_name: str,
        username: str = "",
        password: str = "",
        verify_ssl: bool = True,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure lazy HTTP resources and validate the index name."""
        if not _INDEX_NAME_PATTERN.fullmatch(index_name):
            raise ValueError("OpenSearch index name contains unsupported characters")
        self._url = url.rstrip("/")
        self._index_name = index_name
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        """Return whether an OpenSearch endpoint is configured."""
        return bool(self._url)

    def _get_client(self) -> httpx.AsyncClient:
        """Create the shared async client on first use."""
        if self._client is None:
            auth = httpx.BasicAuth(self._username, self._password) if self._username else None
            self._client = httpx.AsyncClient(
                base_url=f"{self._url}/",
                auth=auth,
                verify=self._verify_ssl,
                timeout=self._timeout_seconds,
            )
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a bounded retried request for transient transport and HTTP errors."""
        client = self._get_client()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(2),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=1),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response
        raise RuntimeError("OpenSearch request exhausted without a response")

    async def initialize(self) -> None:
        """Create the BM25 index and its stable mapping when it does not exist."""
        if not self.enabled:
            return
        client = self._get_client()
        response = await client.head(self._index_name)
        if response.status_code == 200:
            return
        if response.status_code != 404:
            response.raise_for_status()

        mapping = {
            "settings": {
                "index": {
                    "similarity": {"default": {"type": "BM25", "k1": 1.2, "b": 0.75}},
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                }
            },
            "mappings": {
                "dynamic": False,
                "properties": {
                    "chunk_id": {"type": "long"},
                    "document_id": {"type": "long"},
                    "content": {"type": "text", "similarity": "BM25"},
                    "title": {"type": "text", "similarity": "BM25"},
                    "source": {"type": "text", "similarity": "BM25"},
                    "source_type": {"type": "keyword"},
                    "space_slug": {"type": "keyword"},
                    "is_public": {"type": "boolean"},
                    "allowed_principals": {"type": "keyword"},
                    "metadata": {"type": "object", "enabled": False},
                },
            },
        }
        await self._request("PUT", self._index_name, json=mapping)
        logger.info("opensearch_index_initialized", index_name=self._index_name)

    async def index_records(self, records: list[SearchIndexRecord]) -> None:
        """Idempotently bulk-upsert chunk records using stable chunk IDs."""
        if not self.enabled or not records:
            return
        lines: list[str] = []
        for record in records:
            lines.append(json.dumps({"index": {"_index": self._index_name, "_id": str(record.chunk_id)}}))
            payload = asdict(record)
            payload["allowed_principals"] = list(record.allowed_principals)
            lines.append(json.dumps(payload, ensure_ascii=False))
        response = await self._request(
            "POST",
            "_bulk",
            content="\n".join(lines) + "\n",
            headers={"Content-Type": "application/x-ndjson"},
            params={"refresh": "wait_for"},
        )
        body = response.json()
        if body.get("errors"):
            raise RuntimeError("OpenSearch bulk indexing reported item failures")
        logger.info("opensearch_chunks_indexed", count=len(records))

    async def delete_document(self, document_id: int) -> None:
        """Remove all indexed chunks for one normalized document."""
        if not self.enabled:
            return
        await self._request(
            "POST",
            f"{self._index_name}/_delete_by_query",
            json={"query": {"term": {"document_id": document_id}}},
        )

    async def delete_space(self, space_slug: str) -> None:
        """Remove every indexed chunk belonging to one knowledge space."""
        if not self.enabled:
            return
        await self._request(
            "POST",
            f"{self._index_name}/_delete_by_query",
            json={"query": {"term": {"space_slug": space_slug}}},
        )

    async def search(
        self,
        query: str,
        context: RetrievalContext,
        *,
        top_k: int,
    ) -> list[SearchIndexHit]:
        """Retrieve strict BM25 candidates with ACL and space filters."""
        if not self.enabled or not query.strip():
            return []
        principals = [f"{kind}:{identifier}" for kind, identifier in context.principals]
        filters: list[dict[str, Any]] = [
            {
                "bool": {
                    "should": [
                        {"term": {"is_public": True}},
                        {"terms": {"allowed_principals": principals}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        ]
        if context.space_slugs:
            filters.append({"terms": {"space_slug": list(context.space_slugs)}})
        body = {
            "size": top_k,
            "_source": False,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["title^2", "content", "source"],
                                "type": "best_fields",
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
        }
        with trace_span("opensearch.bm25", top_k=top_k):
            response = await self._request("POST", f"{self._index_name}/_search", json=body)
        raw_hits = response.json().get("hits", {}).get("hits", [])
        hits = [SearchIndexHit(chunk_id=int(hit["_id"]), score=float(hit.get("_score") or 0.0)) for hit in raw_hits]
        logger.debug("opensearch_search_completed", hits=len(hits), query_length=len(query))
        return hits

    async def close(self) -> None:
        """Close the internally owned HTTP client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


search_index_service = OpenSearchBM25Service(
    url=settings.OPENSEARCH_URL,
    index_name=settings.OPENSEARCH_INDEX,
    username=settings.OPENSEARCH_USERNAME,
    password=settings.OPENSEARCH_PASSWORD,
    verify_ssl=settings.OPENSEARCH_VERIFY_SSL,
    timeout_seconds=settings.OPENSEARCH_TIMEOUT,
)
