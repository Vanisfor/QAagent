"""Cross-Encoder reranking with bounded fail-open behavior."""

from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import KnowledgeHit


class RerankerService:
    """Call a SiliconFlow-compatible rerank endpoint and validate its indices."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        enabled: bool,
        timeout_seconds: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure lazy HTTP resources without sending provider traffic."""
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        """Return whether reranking is enabled and credentialed."""
        return self._enabled and bool(self._api_key)

    def _get_client(self) -> httpx.AsyncClient:
        """Create the provider client on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{self._base_url}/",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_seconds,
            )
        return self._client

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a bounded retried rerank request and return parsed JSON."""
        client = self._get_client()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(2),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=1),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                response = await client.post(f"{self._base_url}/rerank", json=payload)
                response.raise_for_status()
                return response.json()
        raise RuntimeError("rerank request exhausted without a response")

    async def rerank(self, query: str, hits: list[KnowledgeHit], *, top_n: int) -> list[KnowledgeHit]:
        """Rerank candidates or safely preserve fused order on any provider error."""
        limit = min(max(1, top_n), len(hits)) if hits else 0
        if limit == 0:
            return []
        if not self.enabled:
            return hits[:limit]

        payload = {
            "model": self._model,
            "query": query,
            "documents": [hit.content for hit in hits],
            "top_n": limit,
            "return_documents": False,
        }
        try:
            with trace_span("reranker.call", model=self._model, candidate_count=len(hits), top_n=limit):
                response = await self._request(payload)
            results = response.get("results")
            if not isinstance(results, list):
                raise RuntimeError("rerank response is missing results")

            reranked: list[KnowledgeHit] = []
            seen: set[int] = set()
            for result in results:
                index = int(result["index"])
                if index < 0 or index >= len(hits) or index in seen:
                    raise RuntimeError("rerank response contains an invalid candidate index")
                seen.add(index)
                score = float(result["relevance_score"])
                hit = hits[index]
                reranked.append(
                    hit.model_copy(
                        update={
                            "score": score,
                            "retrieval_scores": {**hit.retrieval_scores, "reranker": score},
                        }
                    )
                )
            if len(reranked) != limit:
                raise RuntimeError("rerank response returned an unexpected result count")
            logger.info("knowledge_reranked", candidates=len(hits), returned=len(reranked), model=self._model)
            return reranked
        except Exception as error:
            logger.exception("knowledge_rerank_failed", error_type=type(error).__name__)
            return hits[:limit]

    async def close(self) -> None:
        """Close the internally owned HTTP client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


reranker_service = RerankerService(
    api_key=settings.RERANK_API_KEY,
    base_url=settings.RERANK_BASE_URL,
    model=settings.RERANK_MODEL,
    enabled=settings.RERANK_ENABLED,
    timeout_seconds=settings.RERANK_TIMEOUT,
)
