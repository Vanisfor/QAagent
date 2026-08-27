"""Long-term memory service using mem0 and pgvector with optional cache layer."""

from mem0 import AsyncMemory

from app.core.cache import (
    cache_key,
    cache_service,
)
from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span


class MemoryService:
    """Service for managing long-term memory using mem0 and pgvector."""

    def __init__(self):
        """Initialize the memory service."""
        self._memory: AsyncMemory | None = None
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_stats(self) -> dict[str, int | float | str]:
        """Return process-local memory cache statistics."""
        total = self._cache_hits + self._cache_misses
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": round(self._cache_hits / total, 4) if total else 0.0,
            "scope": "instance",
        }

    async def _get_memory(self) -> AsyncMemory:
        if self._memory is None:
            self._memory = await AsyncMemory.from_config(
                config_dict={
                    "vector_store": {
                        "provider": "pgvector",
                        "config": {
                            "collection_name": settings.LONG_TERM_MEMORY_COLLECTION_NAME,
                            "dbname": settings.POSTGRES_DB,
                            "user": settings.POSTGRES_USER,
                            "password": settings.POSTGRES_PASSWORD,
                            "host": settings.POSTGRES_HOST,
                            "port": settings.POSTGRES_PORT,
                        },
                    },
                    "llm": {
                        "provider": "deepseek",
                        "config": {
                            "model": settings.LONG_TERM_MEMORY_MODEL,
                            "api_key": settings.DEEPSEEK_API_KEY,
                            "deepseek_base_url": settings.DEEPSEEK_BASE_URL,
                            "max_tokens": settings.MAX_TOKENS,
                        },
                    },
                    "embedder": {
                        "provider": "openai",
                        "config": {
                            "model": settings.LONG_TERM_MEMORY_EMBEDDER_MODEL,
                            "api_key": settings.SILICONFLOW_API_KEY,
                            "openai_base_url": settings.SILICONFLOW_BASE_URL,
                            "embedding_dims": settings.EMBEDDING_DIM,
                        },
                    },
                }
            )
        return self._memory

    async def initialize(self) -> None:
        """Pre-warm the mem0 AsyncMemory instance and its pgvector connection pool.

        Call once at startup so the first search() or add() doesn't pay the
        ~130ms from_config + pgvector.list_cols() cold-init cost.
        """
        await self._get_memory()
        logger.info("memory_service_initialized")

    async def search(self, user_id: str | None, query: str) -> str:
        """Search relevant memories for a user.

        Checks cache first; on miss, queries mem0 and caches the result.

        Returns formatted memory string, or empty string on failure or when
        no user_id is supplied (anonymous sessions skip long-term memory
        rather than pooling under a shared partition).
        """
        if user_id is None:
            return ""
        with trace_span("memory.search") as span:
            try:
                key = cache_key("memory", str(user_id), query)
                cached = await cache_service.get(key)
                if cached is not None:
                    self._cache_hits += 1
                    span.set_attribute("cache_hit", True)
                    logger.debug("memory_search_cache_hit", user_id=user_id)
                    return cached

                span.set_attribute("cache_hit", False)
                self._cache_misses += 1
                memory = await self._get_memory()
                results = await memory.search(user_id=str(user_id), query=query)
                memories = results["results"]
                span.set_attribute("result_count", len(memories))
                result = "\n".join([f"* {r['memory']}" for r in memories])
                # An empty result is still a successful lookup. Caching it avoids
                # repeatedly calling mem0 for the same query during the TTL.
                await cache_service.set(key, result)
                return result
            except Exception as e:
                span.record_error(e)
                logger.exception(
                    "failed_to_get_relevant_memory",
                    error_type=type(e).__name__,
                    user_id=user_id,
                    query_length=len(query),
                )
                return ""

    async def add(self, user_id: str | None, messages: list[dict], metadata: dict | None = None) -> None:
        """Add messages to long-term memory for a user.

        No-op when ``user_id`` is ``None`` (see ``search`` for rationale).
        """
        if user_id is None:
            return
        with trace_span("memory.persist", detached=True) as span:
            try:
                memory = await self._get_memory()
                result = await memory.add(messages, user_id=str(user_id), metadata=metadata)
                result_count = len(result.get("results", [])) if isinstance(result, dict) else 0
                span.set_attribute("result_count", result_count)
                logger.info("long_term_memory_updated_successfully", user_id=user_id)
            except Exception as e:
                span.record_error(e)
                logger.exception("failed_to_update_long_term_memory", user_id=user_id, error=str(e))


memory_service = MemoryService()
