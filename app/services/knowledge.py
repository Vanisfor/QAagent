"""Knowledge base service for RAG-based Q&A.

This service manages the pgvector-backed knowledge base:

- **Embedding**: free SiliconFlow (SiliconCloud) embeddings via the OpenAI
  compatible ``/embeddings`` endpoint (default ``BAAI/bge-m3``, 1024 dims).
  Calls are retried with tenacity (exponential backoff), bounded by a
  per-request timeout, locally traced, and batched to stay under
  provider request limits.
- **Storage**: chunks are stored in a ``knowledge_chunks`` table (created by
  the Alembic migration) with a ``vector`` column, an HNSW cosine index and a
  ``(source, content_hash)`` unique constraint for idempotent ingestion.
- **Retrieval**: queries are embedded and matched with cosine similarity.

The service degrades gracefully: when ``SILICONFLOW_API_KEY`` is not set,
``search`` raises :class:`KnowledgeBaseNotConfigured` so the agent tool can
report a friendly message instead of crashing the conversation.
"""

import asyncio
import hashlib
import json
import logging
import math
from typing import (
    List,
    Optional,
)
from urllib.parse import quote_plus

from openai import (
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from psycopg import (
    AsyncConnection,
    sql,
)
from psycopg.rows import (
    DictRow,
    dict_row,
)
from psycopg_pool import AsyncConnectionPool
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import (
    DocumentChunk,
    KnowledgeHit,
)

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]
_MAX_SEARCH_RESULTS = 20


def _vector_literal(vector: List[float]) -> str:
    """Format a float list as a pgvector literal, e.g. ``'[0.1,0.2]'``.

    Explicit bracket syntax avoids relying on psycopg's array rendering
    (``{...}``) being accepted by the pgvector input parser.
    """
    return "[" + ",".join(str(value) for value in vector) + "]"


def _content_hash(content: str) -> str:
    """Return a stable short hash of a chunk's content for deduplication."""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _validate_embeddings(embeddings: List[List[float]], expected_count: int) -> None:
    """Validate provider output before it reaches pgvector."""
    if len(embeddings) != expected_count:
        raise RuntimeError(f"embedding count mismatch: received {len(embeddings)} vectors for {expected_count} texts")

    for index, embedding in enumerate(embeddings):
        if len(embedding) != settings.EMBEDDING_DIM:
            raise RuntimeError(
                f"embedding dimension mismatch at index {index}: received {len(embedding)}, "
                f"expected {settings.EMBEDDING_DIM}"
            )
        if not all(math.isfinite(value) for value in embedding):
            raise RuntimeError(f"embedding at index {index} contains non-finite values")


class KnowledgeBaseNotConfigured(Exception):
    """Raised when the knowledge base cannot be used (e.g. missing API key)."""


class KnowledgeService:
    """Manage document ingestion, storage and retrieval for the knowledge base."""

    def __init__(self) -> None:
        """Initialize the knowledge service with lazy resources."""
        self._pool: Optional[PostgresConnPool] = None
        self._client: Optional[AsyncOpenAI] = None
        # Guards lazy pool creation so concurrent first calls cannot
        # create (and leak) more than one connection pool.
        self._pool_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _get_client(self) -> AsyncOpenAI:
        """Return the OpenAI-compatible client (SiliconFlow).

        Raises:
            KnowledgeBaseNotConfigured: When ``SILICONFLOW_API_KEY`` is missing.
        """
        if not settings.SILICONFLOW_API_KEY:
            raise KnowledgeBaseNotConfigured(
                "knowledge base is not configured: set SILICONFLOW_API_KEY in your .env file"
            )
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.SILICONFLOW_API_KEY,
                base_url=settings.SILICONFLOW_BASE_URL,
                timeout=settings.EMBEDDING_TIMEOUT,
                max_retries=0,  # retries are handled by tenacity below
            )
        return self._client

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts in batches using the configured embedding model.

        Args:
            texts: Texts to embed.

        Returns:
            A list of embedding vectors, one per input text (input order preserved).

        Raises:
            KnowledgeBaseNotConfigured: When no API key is configured.
            Exception: When the embedding API call fails after retries.
        """
        if not texts:
            return []
        self._get_client()  # fail fast when not configured

        results: List[List[float]] = []
        for start in range(0, len(texts), settings.EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + settings.EMBEDDING_BATCH_SIZE]
            results.extend(await self._embed_batch(batch))
        return results

    @retry(
        stop=stop_after_attempt(settings.MAX_LLM_CALL_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError, asyncio.TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed one batch of texts with retry and local tracing.

        Args:
            texts: Batch of texts (never empty).

        Returns:
            Embedding vectors in input order.

        Raises:
            Exception: After all retry attempts are exhausted.
        """
        client = self._get_client()
        with trace_span(
            "embedding.batch",
            model=settings.EMBEDDING_MODEL,
            batch_size=len(texts),
            dimensions=settings.EMBEDDING_DIM,
        ):
            response = await client.embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=texts,
            )
            # Preserve input order (some providers may reorder results).
            ordered = sorted(response.data, key=lambda item: item.index)
            embeddings = [item.embedding for item in ordered]
            _validate_embeddings(embeddings, len(texts))
            logger.debug(
                "embeddings_generated",
                count=len(embeddings),
                model=settings.EMBEDDING_MODEL,
            )
            return embeddings

    # ------------------------------------------------------------------
    # Database connection
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Pre-create the connection pool (call once at application startup)."""
        await self._get_pool()
        logger.info("knowledge_service_initialized")

    async def _get_pool(self) -> PostgresConnPool:
        """Get (or create) the PostgreSQL connection pool for the knowledge table.

        Creation is guarded by an ``asyncio.Lock`` so concurrent first callers
        cannot create multiple pools (double-checked locking).
        """
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    connection_url = (
                        "postgresql://"
                        f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
                    )
                    # Small pool: the knowledge table is lightly used compared
                    # to the checkpointer and SQLModel pools.
                    self._pool = AsyncConnectionPool(
                        connection_url,
                        open=False,
                        min_size=0,
                        max_size=min(5, settings.POSTGRES_POOL_SIZE),
                        kwargs={
                            "autocommit": True,
                            "connect_timeout": 5,
                            "prepare_threshold": None,
                            "row_factory": dict_row,
                        },
                    )
                    await self._pool.open()
                    logger.info("knowledge_connection_pool_created", max_size=self._pool.max_size)
        return self._pool

    async def close(self) -> None:
        """Close the connection pool and the embedding client (on shutdown)."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("knowledge_connection_pool_closed")
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("knowledge_embedding_client_closed")

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def add_documents(self, chunks: List[DocumentChunk]) -> int:
        """Embed and store document chunks in the knowledge base.

        Insertion runs in a single transaction; chunks that already exist
        (same source and content hash) are skipped, making ingestion
        idempotent.

        Args:
            chunks: The chunks to store.

        Returns:
            The number of newly inserted chunks.

        Raises:
            KnowledgeBaseNotConfigured: When no embedding API key is configured.
            RuntimeError: When the embedding API returns a mismatched count.
        """
        if not chunks:
            return 0

        embeddings = await self.embed([chunk.content for chunk in chunks])
        _validate_embeddings(embeddings, len(chunks))

        pool = await self._get_pool()
        insert_stmt = sql.SQL(
            """
            INSERT INTO {} (content, source, content_hash, metadata, embedding)
            VALUES (%s, %s, %s, %s::jsonb, %s::vector)
            ON CONFLICT (source, content_hash) DO NOTHING
            """
        ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))

        inserted = 0
        async with pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    for chunk, embedding in zip(chunks, embeddings, strict=True):
                        await cur.execute(
                            insert_stmt,
                            (
                                chunk.content,
                                chunk.source,
                                _content_hash(chunk.content),
                                json.dumps(chunk.metadata, ensure_ascii=False),
                                _vector_literal(embedding),
                            ),
                        )
                        inserted += cur.rowcount or 0

        logger.info(
            "knowledge_documents_added",
            count=inserted,
            skipped=len(chunks) - inserted,
            model=settings.EMBEDDING_MODEL,
        )
        return inserted

    async def replace_source(self, chunks: List[DocumentChunk]) -> int:
        """Atomically replace all stored chunks for one source document.

        Embeddings are generated before the transaction starts. If embedding
        fails, the existing searchable document remains untouched.
        """
        if not chunks:
            return 0

        sources = {chunk.source for chunk in chunks}
        if len(sources) != 1:
            raise ValueError("replace_source requires all chunks to have the same source")

        embeddings = await self.embed([chunk.content for chunk in chunks])
        _validate_embeddings(embeddings, len(chunks))
        source = chunks[0].source
        pool = await self._get_pool()
        delete_stmt = sql.SQL("DELETE FROM {} WHERE source = %s").format(sql.Identifier(settings.KNOWLEDGE_TABLE))
        insert_stmt = sql.SQL(
            """
            INSERT INTO {} (content, source, content_hash, metadata, embedding)
            VALUES (%s, %s, %s, %s::jsonb, %s::vector)
            ON CONFLICT (source, content_hash) DO NOTHING
            """
        ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))

        inserted = 0
        async with pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(delete_stmt, (source,))
                    for chunk, embedding in zip(chunks, embeddings, strict=True):
                        await cur.execute(
                            insert_stmt,
                            (
                                chunk.content,
                                chunk.source,
                                _content_hash(chunk.content),
                                json.dumps(chunk.metadata, ensure_ascii=False),
                                _vector_literal(embedding),
                            ),
                        )
                        inserted += cur.rowcount or 0

        logger.info("knowledge_source_replaced", source=source, chunks=inserted)
        return inserted

    async def delete_source(self, source: str) -> int:
        """Delete all chunks belonging to a source document.

        Args:
            source: The source identifier to delete.

        Returns:
            The number of deleted rows.
        """
        pool = await self._get_pool()
        delete_stmt = sql.SQL("DELETE FROM {} WHERE source = %s").format(sql.Identifier(settings.KNOWLEDGE_TABLE))

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(delete_stmt, (source,))
                deleted = cur.rowcount

        logger.info("knowledge_source_deleted", source=source, deleted=deleted)
        return deleted

    async def reset(self) -> int:
        """Delete all chunks from the knowledge base.

        Returns:
            The number of deleted rows.
        """
        pool = await self._get_pool()
        delete_stmt = sql.SQL("DELETE FROM {}").format(sql.Identifier(settings.KNOWLEDGE_TABLE))

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(delete_stmt)
                deleted = cur.rowcount

        logger.info("knowledge_base_reset", deleted=deleted)
        return deleted

    async def count(self) -> int:
        """Return the total number of chunks in the knowledge base.

        Returns:
            The chunk count (0 when the table does not exist yet).
        """
        pool = await self._get_pool()
        count_stmt = sql.SQL("SELECT count(*) AS total FROM {}").format(sql.Identifier(settings.KNOWLEDGE_TABLE))

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(count_stmt)
                row = await cur.fetchone()

        return int(row["total"]) if row else 0

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
    ) -> List[KnowledgeHit]:
        """Retrieve the most relevant chunks for a query.

        Args:
            query: The user question.
            top_k: Maximum number of hits to return (defaults to ``KNOWLEDGE_TOP_K``).
            min_similarity: Cosine similarity cutoff in ``[0, 1]``
                (defaults to ``KNOWLEDGE_MIN_SIMILARITY``).

        Returns:
            The top matching chunks ordered by similarity (descending).

        Raises:
            KnowledgeBaseNotConfigured: When no embedding API key is configured.
        """
        if not query.strip():
            return []

        requested_k = top_k if top_k is not None else settings.KNOWLEDGE_TOP_K
        k = min(max(1, requested_k), _MAX_SEARCH_RESULTS)
        if min_similarity is not None:
            threshold = min(max(min_similarity, 0.0), 1.0)
        else:
            threshold = min(max(settings.KNOWLEDGE_MIN_SIMILARITY, 0.0), 1.0)

        with trace_span("rag.search", top_k=k, similarity_threshold=threshold) as rag_span:
            query_embedding = (await self.embed([query]))[0]
            query_vector = _vector_literal(query_embedding)
            pool = await self._get_pool()

            search_stmt = sql.SQL(
                """
                SELECT content, source, metadata,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM {}
                WHERE 1 - (embedding <=> %s::vector) >= %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """
            ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))

            with trace_span("pgvector.search", top_k=k):
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(search_stmt, (query_vector, query_vector, threshold, query_vector, k))
                        rows = await cur.fetchall()

            hits = [
                KnowledgeHit(
                    content=row["content"],
                    source=row["source"],
                    metadata=row["metadata"] or {},
                    similarity=float(row["similarity"]),
                )
                for row in rows
            ]

            rag_span.set_attribute("hit_count", len(hits))
            rag_span.set_attribute("source_count", len({hit.source for hit in hits}))
            logger.debug("knowledge_search_completed", query_length=len(query), hits=len(hits), top_k=k)
            return hits


# Singleton instance
knowledge_service = KnowledgeService()
