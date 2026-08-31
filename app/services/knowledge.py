"""Knowledge base service for RAG-based Q&A.

This service manages the ACL-aware hybrid knowledge base:

- **Embedding**: free SiliconFlow (SiliconCloud) embeddings via the OpenAI
  compatible ``/embeddings`` endpoint (default ``BAAI/bge-m3``, 1024 dims).
  Calls are retried with tenacity (exponential backoff), bounded by a
  per-request timeout, locally traced, and batched to stay under
  provider request limits.
- **Storage**: normalized spaces/documents/chunks are stored in PostgreSQL with
  additive user/group ACLs and space-scoped idempotency.
- **Retrieval**: pgvector dense and OpenSearch BM25 candidates are ACL-filtered,
  fused with RRF, post-validated in PostgreSQL and optionally reranked.

The service degrades gracefully: when ``SILICONFLOW_API_KEY`` is not set,
``search`` raises :class:`KnowledgeBaseNotConfigured` so the agent tool can
report a friendly message instead of crashing the conversation.
"""

import asyncio
import hashlib
import json
import logging
import math
from datetime import datetime
from typing import (
    Any,
    Dict,
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
    RetrievalContext,
)
from app.services.reranker import reranker_service
from app.services.search_index import SearchIndexRecord, search_index_service

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


def _document_hash(chunks: List[DocumentChunk]) -> str:
    """Return a stable hash for one ordered document version."""
    payload = "".join(_content_hash(chunk.content) for chunk in chunks)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _reciprocal_rank_fusion(rankings: List[List[int]], rrf_k: int = 60) -> Dict[int, float]:
    """Fuse ranked candidate IDs without comparing incompatible raw scores."""
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")

    scores: Dict[int, float] = {}
    for ranking in rankings:
        seen: set[int] = set()
        unique_ranking: List[int] = []
        for candidate_id in ranking:
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            unique_ranking.append(candidate_id)
        for rank, candidate_id in enumerate(unique_ranking, start=1):
            scores[candidate_id] = scores.get(candidate_id, 0.0) + 1.0 / (rrf_k + rank)
    return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0])))


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


class KnowledgeSpaceNotFound(Exception):
    """Raised when ingestion targets a knowledge space that does not exist."""


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
        await search_index_service.initialize()
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
        await search_index_service.close()
        await reranker_service.close()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def create_space(
        self,
        slug: str,
        name: str,
        *,
        is_public: bool = False,
        owner_user_id: Optional[str] = None,
        organization_id: int = 1,
    ) -> int:
        """Create a knowledge space and optionally grant its owner access."""
        normalized_slug = slug.strip().lower()
        if not normalized_slug or not name.strip():
            raise ValueError("knowledge-space slug and name are required")

        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO knowledge_spaces (slug, name, is_public, organization_id)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (slug) DO UPDATE
                        SET name = EXCLUDED.name,
                            updated_at = now()
                        WHERE knowledge_spaces.organization_id = EXCLUDED.organization_id
                        RETURNING id
                        """,
                        (normalized_slug, name.strip(), is_public, organization_id),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        raise RuntimeError("knowledge space creation returned no identifier")
                    space_id = int(row["id"])
                    if owner_user_id is not None:
                        await cur.execute(
                            """
                            INSERT INTO knowledge_space_principals (
                                space_id, principal_type, principal_id, role
                            )
                            VALUES (%s, 'user', %s, 'owner')
                            ON CONFLICT (space_id, principal_type, principal_id)
                            DO UPDATE SET role = 'owner'
                            """,
                            (space_id, str(owner_user_id)),
                        )

        logger.info("knowledge_space_created", space_id=space_id, is_public=is_public)
        return space_id

    async def grant_space_access(
        self,
        space_slug: str,
        principal_type: str,
        principal_id: str,
        *,
        role: str = "reader",
    ) -> None:
        """Grant additive space access to a user or external group principal."""
        if principal_type not in {"user", "group"}:
            raise ValueError("principal_type must be 'user' or 'group'")
        if role not in {"reader", "editor", "owner"}:
            raise ValueError("role must be reader, editor or owner")

        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO knowledge_space_principals (
                        space_id, principal_type, principal_id, role
                    )
                    SELECT id, %s, %s, %s
                    FROM knowledge_spaces
                    WHERE slug = %s
                    ON CONFLICT (space_id, principal_type, principal_id)
                    DO UPDATE SET role = EXCLUDED.role
                    """,
                    (principal_type, principal_id, role, space_slug),
                )
                if cur.rowcount == 0:
                    raise KnowledgeSpaceNotFound(f"knowledge space not found: {space_slug}")

        logger.info("knowledge_space_access_granted", principal_type=principal_type, role=role)
        await self.reindex_space(space_slug)

    async def delete_space(self, space_slug: str) -> bool:
        """Delete a non-default knowledge space and all of its documents."""
        if space_slug == settings.KNOWLEDGE_DEFAULT_SPACE:
            raise ValueError("the default knowledge space cannot be deleted")

        await search_index_service.delete_space(space_slug)
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM knowledge_spaces WHERE slug = %s RETURNING id",
                    (space_slug,),
                )
                deleted = await cur.fetchone() is not None

        logger.info("knowledge_space_deleted", deleted=deleted)
        return deleted

    async def add_documents(
        self,
        chunks: List[DocumentChunk],
        *,
        space_slug: Optional[str] = None,
        source_type: str = "local",
    ) -> int:
        """Replace every source represented by the supplied document chunks."""
        grouped: Dict[str, List[DocumentChunk]] = {}
        for chunk in chunks:
            grouped.setdefault(chunk.source, []).append(chunk)

        inserted = 0
        for source_chunks in grouped.values():
            inserted += await self.replace_source(
                source_chunks,
                space_slug=space_slug,
                source_type=source_type,
            )
        return inserted

    async def _load_index_records(self, document_id: int) -> List[SearchIndexRecord]:
        """Load one document's chunks with effective additive ACL principals."""
        if not search_index_service.enabled:
            return []
        pool = await self._get_pool()
        statement = sql.SQL(
            """
            SELECT chunk.id AS chunk_id, chunk.document_id, chunk.content,
                   document.title, document.source, document.source_type,
                   space.slug AS space_slug, space.is_public, space.organization_id,
                   document.metadata || chunk.metadata AS metadata,
                   ARRAY(
                       SELECT principal_type || ':' || principal_id
                       FROM knowledge_space_principals
                       WHERE space_id = space.id
                       UNION
                       SELECT principal_type || ':' || principal_id
                       FROM knowledge_document_principals
                       WHERE document_id = document.id
                   ) AS allowed_principals
            FROM {} AS chunk
            JOIN knowledge_documents AS document ON document.id = chunk.document_id
            JOIN knowledge_spaces AS space ON space.id = chunk.space_id
            WHERE document.id = %s AND document.deleted_at IS NULL
            ORDER BY chunk.id
            """
        ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(statement, (document_id,))
                rows = await cur.fetchall()
        return [
            SearchIndexRecord(
                chunk_id=int(row["chunk_id"]),
                document_id=int(row["document_id"]),
                content=str(row["content"]),
                title=str(row["title"]),
                source=str(row["source"]),
                source_type=str(row["source_type"]),
                space_slug=str(row["space_slug"]),
                is_public=bool(row["is_public"]),
                allowed_principals=tuple(str(value) for value in (row["allowed_principals"] or [])),
                metadata=dict(row["metadata"] or {}),
                organization_id=int(row["organization_id"]),
            )
            for row in rows
        ]

    async def _index_document(self, document_id: int) -> None:
        """Refresh one document in the optional strict BM25 index."""
        if not search_index_service.enabled:
            return
        await search_index_service.delete_document(document_id)
        await search_index_service.index_records(await self._load_index_records(document_id))

    async def reindex_space(self, space_slug: str) -> int:
        """Refresh all active documents in one space in the strict BM25 index."""
        if not search_index_service.enabled:
            return 0
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT document.id
                    FROM knowledge_documents AS document
                    JOIN knowledge_spaces AS space ON space.id = document.space_id
                    WHERE space.slug = %s AND document.deleted_at IS NULL
                    ORDER BY document.id
                    """,
                    (space_slug,),
                )
                document_ids = [int(row["id"]) for row in await cur.fetchall()]
        await search_index_service.delete_space(space_slug)
        for document_id in document_ids:
            await self._index_document(document_id)
        logger.info("knowledge_space_reindexed", documents=len(document_ids))
        return len(document_ids)

    async def replace_source(
        self,
        chunks: List[DocumentChunk],
        *,
        space_slug: Optional[str] = None,
        source_type: str = "local",
        external_id: Optional[str] = None,
        connector_id: Optional[int] = None,
        document_metadata: Optional[Dict[str, Any]] = None,
        source_updated_at: Optional[datetime] = None,
    ) -> int:
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
        external_document_id = external_id or source
        target_space = space_slug or settings.KNOWLEDGE_DEFAULT_SPACE
        stored_metadata = {**(document_metadata or {}), "chunk_count": len(chunks)}
        pool = await self._get_pool()
        insert_stmt = sql.SQL(
            """
            INSERT INTO {} (
                space_id, document_id, content, source, content_hash, metadata, embedding
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::vector)
            ON CONFLICT (document_id, content_hash) DO NOTHING
            """
        ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))

        inserted = 0
        async with pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO knowledge_documents (
                            space_id, source_type, external_id, source, title,
                            content_hash, metadata, connector_id, source_updated_at, deleted_at
                        )
                        SELECT id, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, NULL
                        FROM knowledge_spaces
                        WHERE slug = %s
                        ON CONFLICT (space_id, source_type, external_id) DO UPDATE
                        SET source = EXCLUDED.source,
                            title = EXCLUDED.title,
                            content_hash = EXCLUDED.content_hash,
                            metadata = EXCLUDED.metadata,
                            connector_id = EXCLUDED.connector_id,
                            source_updated_at = EXCLUDED.source_updated_at,
                            deleted_at = NULL,
                            updated_at = now()
                        RETURNING id, space_id
                        """,
                        (
                            source_type,
                            external_document_id,
                            source,
                            source,
                            _document_hash(chunks),
                            json.dumps(stored_metadata, ensure_ascii=False),
                            connector_id,
                            source_updated_at,
                            target_space,
                        ),
                    )
                    document = await cur.fetchone()
                    if document is None:
                        raise KnowledgeSpaceNotFound(f"knowledge space not found: {target_space}")
                    document_id = int(document["id"])
                    space_id = int(document["space_id"])
                    await cur.execute(
                        sql.SQL("DELETE FROM {} WHERE document_id = %s").format(
                            sql.Identifier(settings.KNOWLEDGE_TABLE)
                        ),
                        (document_id,),
                    )
                    for chunk, embedding in zip(chunks, embeddings, strict=True):
                        await cur.execute(
                            insert_stmt,
                            (
                                space_id,
                                document_id,
                                chunk.content,
                                chunk.source,
                                _content_hash(chunk.content),
                                json.dumps(chunk.metadata, ensure_ascii=False),
                                _vector_literal(embedding),
                            ),
                        )
                        inserted += cur.rowcount or 0

        await self._index_document(document_id)
        logger.info("knowledge_source_replaced", space_slug=target_space, source_type=source_type, chunks=inserted)
        return inserted

    async def delete_source(
        self,
        source: str,
        *,
        space_slug: Optional[str] = None,
        source_type: str = "local",
    ) -> int:
        """Delete all chunks belonging to a source document.

        Args:
            source: The source identifier to delete.
            space_slug: Knowledge-space slug; defaults to the configured public space.
            source_type: Connector/source type used to identify the document.

        Returns:
            The number of deleted rows.
        """
        pool = await self._get_pool()
        target_space = space_slug or settings.KNOWLEDGE_DEFAULT_SPACE

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    sql.SQL(
                        """
                        WITH target AS (
                            SELECT document.id
                            FROM knowledge_documents AS document
                            JOIN knowledge_spaces AS space ON space.id = document.space_id
                            WHERE space.slug = %s
                              AND document.source_type = %s
                              AND document.external_id = %s
                        ), deleted_chunks AS (
                            DELETE FROM {} AS chunk
                            USING target
                            WHERE chunk.document_id = target.id
                            RETURNING chunk.id
                        ), deleted_document AS (
                            UPDATE knowledge_documents AS document
                            SET deleted_at = now(), updated_at = now()
                            FROM target
                            WHERE document.id = target.id
                            RETURNING document.id
                        )
                        SELECT id AS document_id,
                               (SELECT count(*) FROM deleted_chunks) AS chunk_count
                        FROM deleted_document
                        """
                    ).format(sql.Identifier(settings.KNOWLEDGE_TABLE)),
                    (target_space, source_type, source),
                )
                row = await cur.fetchone()
                deleted = int(row["chunk_count"]) if row else 0
                document_id = int(row["document_id"]) if row else None

        if document_id is not None:
            await search_index_service.delete_document(document_id)

        logger.info("knowledge_source_deleted", space_slug=target_space, source_type=source_type, deleted=deleted)
        return deleted

    async def reset(self, *, space_slug: Optional[str] = None) -> int:
        """Delete all documents from one explicitly scoped knowledge space.

        Returns:
            The number of deleted rows.
        """
        pool = await self._get_pool()
        target_space = space_slug or settings.KNOWLEDGE_DEFAULT_SPACE
        await search_index_service.delete_space(target_space)

        async with pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        sql.SQL(
                            """
                            SELECT count(*) AS total
                            FROM {} AS chunk
                            JOIN knowledge_spaces AS space ON space.id = chunk.space_id
                            WHERE space.slug = %s
                            """
                        ).format(sql.Identifier(settings.KNOWLEDGE_TABLE)),
                        (target_space,),
                    )
                    row = await cur.fetchone()
                    deleted = int(row["total"]) if row else 0
                    await cur.execute(
                        """
                        DELETE FROM knowledge_documents AS document
                        USING knowledge_spaces AS space
                        WHERE document.space_id = space.id
                          AND space.slug = %s
                        """,
                        (target_space,),
                    )

        logger.info("knowledge_space_reset", space_slug=target_space, deleted=deleted)
        return deleted

    async def count(self, *, space_slug: Optional[str] = None) -> int:
        """Return the number of chunks in one knowledge space.

        Returns:
            The chunk count (0 when the table does not exist yet).
        """
        pool = await self._get_pool()
        target_space = space_slug or settings.KNOWLEDGE_DEFAULT_SPACE
        count_stmt = sql.SQL(
            """
            SELECT count(*) AS total
            FROM {} AS chunk
            JOIN knowledge_spaces AS space ON space.id = chunk.space_id
            WHERE space.slug = %s
            """
        ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(count_stmt, (target_space,))
                row = await cur.fetchone()

        return int(row["total"]) if row else 0

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        context: Optional[RetrievalContext] = None,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
    ) -> List[KnowledgeHit]:
        """Retrieve the most relevant chunks for a query.

        Args:
            query: The user question.
            context: Server-authenticated ACL principals and optional space scope.
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

        access_context = context or RetrievalContext(user_id="anonymous", organization_ids=(1,))
        if not access_context.organization_ids:
            raise ValueError("organization context is required for knowledge retrieval")
        group_ids = list(access_context.group_ids)
        organization_ids = list(access_context.organization_ids) or None
        scoped_spaces = list(access_context.space_slugs) or None

        with trace_span("rag.search", top_k=k, similarity_threshold=threshold, retrieval_mode="hybrid") as rag_span:
            bm25_task = (
                asyncio.create_task(
                    search_index_service.search(
                        query,
                        access_context,
                        top_k=min(max(k, settings.KNOWLEDGE_LEXICAL_CANDIDATES), 200),
                    )
                )
                if search_index_service.enabled
                else None
            )
            try:
                query_embedding = (await self.embed([query]))[0]
            except BaseException:
                if bm25_task is not None:
                    bm25_task.cancel()
                    await asyncio.gather(bm25_task, return_exceptions=True)
                raise
            query_vector = _vector_literal(query_embedding)
            pool = await self._get_pool()
            dense_limit = min(max(k, settings.KNOWLEDGE_DENSE_CANDIDATES), 200)
            lexical_limit = min(max(k, settings.KNOWLEDGE_LEXICAL_CANDIDATES), 200)
            dense_stmt = sql.SQL(
                """
                SELECT chunk.id AS chunk_id, chunk.document_id, chunk.content,
                       document.source, space.slug AS space_slug,
                       document.metadata || chunk.metadata AS metadata,
                       1 - (chunk.embedding <=> %s::vector) AS dense_score
                FROM {} AS chunk
                JOIN knowledge_documents AS document ON document.id = chunk.document_id
                JOIN knowledge_spaces AS space ON space.id = chunk.space_id
                WHERE document.deleted_at IS NULL
                  AND 1 - (chunk.embedding <=> %s::vector) >= %s
                  AND (
                      space.is_public
                      OR EXISTS (
                          SELECT 1 FROM knowledge_space_principals AS acl
                          WHERE acl.space_id = space.id
                            AND (
                                (acl.principal_type = 'user' AND acl.principal_id = %s)
                                OR (acl.principal_type = 'group' AND acl.principal_id = ANY(%s::text[]))
                            )
                      )
                      OR EXISTS (
                          SELECT 1 FROM knowledge_document_principals AS acl
                          WHERE acl.document_id = document.id
                            AND (
                                (acl.principal_type = 'user' AND acl.principal_id = %s)
                                OR (acl.principal_type = 'group' AND acl.principal_id = ANY(%s::text[]))
                            )
                      )
                  )
                  AND (%s::text[] IS NULL OR space.slug = ANY(%s))
                  AND (%s::bigint[] IS NULL OR space.organization_id = ANY(%s))
                ORDER BY chunk.embedding <=> %s::vector
                LIMIT %s
                """
            ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))
            lexical_stmt = sql.SQL(
                """
                WITH lexical_query AS (
                    SELECT websearch_to_tsquery('simple', %s) AS query
                )
                SELECT chunk.id AS chunk_id, chunk.document_id, chunk.content,
                       document.source, space.slug AS space_slug,
                       document.metadata || chunk.metadata AS metadata,
                       ts_rank_cd(chunk.search_vector, lexical_query.query) AS lexical_score
                FROM {} AS chunk
                JOIN knowledge_documents AS document ON document.id = chunk.document_id
                JOIN knowledge_spaces AS space ON space.id = chunk.space_id
                CROSS JOIN lexical_query
                WHERE document.deleted_at IS NULL
                  AND chunk.search_vector @@ lexical_query.query
                  AND (
                      space.is_public
                      OR EXISTS (
                          SELECT 1 FROM knowledge_space_principals AS acl
                          WHERE acl.space_id = space.id
                            AND (
                                (acl.principal_type = 'user' AND acl.principal_id = %s)
                                OR (acl.principal_type = 'group' AND acl.principal_id = ANY(%s::text[]))
                            )
                      )
                      OR EXISTS (
                          SELECT 1 FROM knowledge_document_principals AS acl
                          WHERE acl.document_id = document.id
                            AND (
                                (acl.principal_type = 'user' AND acl.principal_id = %s)
                                OR (acl.principal_type = 'group' AND acl.principal_id = ANY(%s::text[]))
                            )
                      )
                  )
                  AND (%s::text[] IS NULL OR space.slug = ANY(%s))
                  AND (%s::bigint[] IS NULL OR space.organization_id = ANY(%s))
                ORDER BY lexical_score DESC, chunk.id
                LIMIT %s
                """
            ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))
            external_lexical_stmt = sql.SQL(
                """
                SELECT chunk.id AS chunk_id, chunk.document_id, chunk.content,
                       document.source, space.slug AS space_slug,
                       document.metadata || chunk.metadata AS metadata
                FROM {} AS chunk
                JOIN knowledge_documents AS document ON document.id = chunk.document_id
                JOIN knowledge_spaces AS space ON space.id = chunk.space_id
                WHERE document.deleted_at IS NULL
                  AND chunk.id = ANY(%s::bigint[])
                  AND (
                      space.is_public
                      OR EXISTS (
                          SELECT 1 FROM knowledge_space_principals AS acl
                          WHERE acl.space_id = space.id
                            AND (
                                (acl.principal_type = 'user' AND acl.principal_id = %s)
                                OR (acl.principal_type = 'group' AND acl.principal_id = ANY(%s::text[]))
                            )
                      )
                      OR EXISTS (
                          SELECT 1 FROM knowledge_document_principals AS acl
                          WHERE acl.document_id = document.id
                            AND (
                                (acl.principal_type = 'user' AND acl.principal_id = %s)
                                OR (acl.principal_type = 'group' AND acl.principal_id = ANY(%s::text[]))
                            )
                      )
                  )
                  AND (%s::text[] IS NULL OR space.slug = ANY(%s))
                  AND (%s::bigint[] IS NULL OR space.organization_id = ANY(%s))
                ORDER BY array_position(%s::bigint[], chunk.id)
                """
            ).format(sql.Identifier(settings.KNOWLEDGE_TABLE))

            external_hits = None
            if bm25_task is not None:
                try:
                    external_hits = await bm25_task
                except Exception as error:
                    logger.exception("opensearch_search_failed_falling_back", error_type=type(error).__name__)
                    rag_span.set_attribute("lexical_fallback", True)

            with trace_span("hybrid.candidates", dense_limit=dense_limit, lexical_limit=lexical_limit):
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            dense_stmt,
                            (
                                query_vector,
                                query_vector,
                                threshold,
                                access_context.user_id,
                                group_ids,
                                access_context.user_id,
                                group_ids,
                                scoped_spaces,
                                scoped_spaces,
                                organization_ids,
                                organization_ids,
                                query_vector,
                                dense_limit,
                            ),
                        )
                        dense_rows = await cur.fetchall()
                        if external_hits is None:
                            await cur.execute(
                                lexical_stmt,
                                (
                                    query,
                                    access_context.user_id,
                                    group_ids,
                                    access_context.user_id,
                                    group_ids,
                                    scoped_spaces,
                                    scoped_spaces,
                                    organization_ids,
                                    organization_ids,
                                    lexical_limit,
                                ),
                            )
                            lexical_rows = await cur.fetchall()
                            lexical_component = "lexical"
                        elif external_hits:
                            external_ids = [hit.chunk_id for hit in external_hits]
                            external_scores = {hit.chunk_id: hit.score for hit in external_hits}
                            await cur.execute(
                                external_lexical_stmt,
                                (
                                    external_ids,
                                    access_context.user_id,
                                    group_ids,
                                    access_context.user_id,
                                    group_ids,
                                    scoped_spaces,
                                    scoped_spaces,
                                    organization_ids,
                                    organization_ids,
                                    external_ids,
                                ),
                            )
                            lexical_rows = await cur.fetchall()
                            for row in lexical_rows:
                                row["lexical_score"] = external_scores[int(row["chunk_id"])]
                            lexical_component = "bm25"
                        else:
                            lexical_rows = []
                            lexical_component = "bm25"

            candidates: Dict[int, Dict[str, Any]] = {}
            for row in dense_rows:
                candidate_id = int(row["chunk_id"])
                candidates[candidate_id] = dict(row)
                candidates[candidate_id]["dense_score"] = float(row["dense_score"])
            for row in lexical_rows:
                candidate_id = int(row["chunk_id"])
                candidate = candidates.setdefault(candidate_id, dict(row))
                candidate["lexical_score"] = float(row["lexical_score"])

            fused = _reciprocal_rank_fusion(
                [
                    [int(row["chunk_id"]) for row in dense_rows],
                    [int(row["chunk_id"]) for row in lexical_rows],
                ],
                rrf_k=settings.KNOWLEDGE_RRF_K,
            )
            max_fused_score = max(fused.values(), default=1.0)
            fusion_limit = min(
                max(k, settings.RERANK_CANDIDATES if reranker_service.enabled else k),
                _MAX_SEARCH_RESULTS,
            )
            hits: List[KnowledgeHit] = []
            for candidate_id, fused_score in list(fused.items())[:fusion_limit]:
                candidate = candidates[candidate_id]
                dense_score = float(candidate.get("dense_score", 0.0))
                lexical_score = float(candidate.get("lexical_score", 0.0))
                hits.append(
                    KnowledgeHit(
                        chunk_id=candidate_id,
                        document_id=int(candidate["document_id"]),
                        content=str(candidate["content"]),
                        source=str(candidate["source"]),
                        space_slug=str(candidate["space_slug"]),
                        metadata=candidate["metadata"] or {},
                        similarity=dense_score,
                        score=fused_score / max_fused_score,
                        retrieval_scores={
                            "dense": dense_score,
                            lexical_component: lexical_score,
                            "rrf": fused_score,
                        },
                    )
                )

            rag_span.set_attribute("hit_count", len(hits))
            rag_span.set_attribute("source_count", len({hit.source for hit in hits}))
            rag_span.set_attribute("dense_candidate_count", len(dense_rows))
            rag_span.set_attribute("lexical_candidate_count", len(lexical_rows))
            rag_span.set_attribute("lexical_backend", lexical_component)
            logger.debug("knowledge_search_completed", query_length=len(query), hits=len(hits), top_k=k)
            return await reranker_service.rerank(query, hits, top_n=k)


# Singleton instance
knowledge_service = KnowledgeService()
