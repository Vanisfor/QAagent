"""PostgreSQL knowledge graph with mandatory chunk provenance and ACL search."""

import json
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import KnowledgeHit, RetrievalContext
from app.schemas.retrieval import GraphExtraction
from app.services.database import database_service
from app.services.llm import LLMService
from app.services.user_llm_settings import UserLLMRuntimeConfig

_GRAPH_EXTRACTION_PROMPT = """Extract a small provenance-ready knowledge graph.
Return canonical entities and directed relations only when explicitly supported.
Each relation must reference the zero-based chunk_index containing its evidence.
Use short lowercase snake_case predicates. Do not infer unsupported relationships.
"""


class KnowledgeGraphService:
    """Extract, persist and ACL-filter provenance-backed graph relations."""

    def __init__(self, llm_factory: Callable[[Any], Any] | None = None) -> None:
        """Accept an injectable structured-output LLM factory."""
        self._llm_factory = llm_factory or (lambda runtime: LLMService(runtime))

    async def replace_document_graph(self, document_id: int, extraction: GraphExtraction) -> int:
        """Atomically replace one document's relations while reusing canonical entities."""
        document_query: Any = text(
            """
            SELECT document.space_id, chunk.id AS chunk_id,
                   CAST(chunk.metadata->>'chunk_index' AS integer) AS chunk_index
            FROM knowledge_documents AS document
            JOIN knowledge_chunks AS chunk ON chunk.document_id = document.id
            WHERE document.id = :document_id AND document.deleted_at IS NULL
            """
        )
        async with database_service.session_factory() as session:
            rows = (await session.exec(document_query, params={"document_id": document_id})).mappings().all()
        if not rows:
            raise ValueError(f"active knowledge document not found: {document_id}")
        space_id = int(rows[0]["space_id"])
        chunks = {int(row["chunk_index"]): int(row["chunk_id"]) for row in rows if row["chunk_index"] is not None}
        missing = sorted({relation.chunk_index for relation in extraction.relations} - set(chunks))
        if missing:
            raise ValueError(f"graph relations reference missing chunk indices: {missing}")

        delete_relations: Any = text("DELETE FROM knowledge_relations WHERE document_id = :document_id")
        upsert_entity: Any = text(
            """
            INSERT INTO knowledge_entities (
                space_id, canonical_name, normalized_name, entity_type, aliases
            )
            VALUES (:space_id, :canonical_name, :normalized_name, :entity_type, CAST(:aliases AS jsonb))
            ON CONFLICT (space_id, entity_type, normalized_name) DO UPDATE
            SET canonical_name = EXCLUDED.canonical_name,
                aliases = EXCLUDED.aliases,
                updated_at = now()
            RETURNING id
            """
        )
        insert_relation: Any = text(
            """
            INSERT INTO knowledge_relations (
                space_id, source_entity_id, target_entity_id, predicate,
                document_id, chunk_id, confidence
            )
            VALUES (
                :space_id, :source_entity_id, :target_entity_id, :predicate,
                :document_id, :chunk_id, :confidence
            )
            ON CONFLICT (
                document_id, chunk_id, source_entity_id, target_entity_id, predicate
            ) DO UPDATE SET confidence = EXCLUDED.confidence
            """
        )
        async with database_service.session_factory() as session, session.begin():
            await session.exec(delete_relations, params={"document_id": document_id})
            entity_ids: dict[str, int] = {}
            for entity in extraction.entities:
                result = await session.exec(
                    upsert_entity,
                    params={
                        "space_id": space_id,
                        "canonical_name": entity.name,
                        "normalized_name": entity.name.casefold(),
                        "entity_type": entity.entity_type,
                        "aliases": json.dumps([alias.casefold() for alias in entity.aliases]),
                    },
                )
                entity_ids[entity.key] = int(result.scalar_one())
            for relation in extraction.relations:
                await session.exec(
                    insert_relation,
                    params={
                        "space_id": space_id,
                        "source_entity_id": entity_ids[relation.source_key],
                        "target_entity_id": entity_ids[relation.target_key],
                        "predicate": relation.predicate,
                        "document_id": document_id,
                        "chunk_id": chunks[relation.chunk_index],
                        "confidence": relation.confidence,
                    },
                )
        logger.info(
            "knowledge_graph_document_replaced",
            document_id=document_id,
            entity_count=len(extraction.entities),
            relation_count=len(extraction.relations),
        )
        return len(extraction.relations)

    async def extract_document(
        self,
        document_id: int,
        *,
        runtime: UserLLMRuntimeConfig | None = None,
    ) -> int:
        """Extract and persist a bounded graph for one document."""
        query: Any = text(
            """
            SELECT document.title, chunk.content,
                   CAST(chunk.metadata->>'chunk_index' AS integer) AS chunk_index
            FROM knowledge_documents AS document
            JOIN knowledge_chunks AS chunk ON chunk.document_id = document.id
            WHERE document.id = :document_id AND document.deleted_at IS NULL
            ORDER BY chunk_index, chunk.id
            LIMIT :chunk_limit
            """
        )
        async with database_service.session_factory() as session:
            rows = (
                await session.exec(
                    query,
                    params={"document_id": document_id, "chunk_limit": settings.KNOWLEDGE_GRAPH_MAX_CHUNKS},
                )
            ).mappings().all()
        if not rows:
            raise ValueError(f"active knowledge document not found: {document_id}")
        rendered_chunks = "\n\n".join(
            f"<chunk index=\"{int(row['chunk_index'])}\">\n{str(row['content'])[:3000]}\n</chunk>"
            for row in rows
            if row["chunk_index"] is not None
        )
        with trace_span("knowledge_graph.extract", document_id=document_id, chunk_count=len(rows)):
            extraction = await self._llm_factory(runtime).call(
                [
                    SystemMessage(content=_GRAPH_EXTRACTION_PROMPT),
                    HumanMessage(content=f"Document: {rows[0]['title']}\n\n{rendered_chunks}"),
                ],
                response_format=GraphExtraction,
                temperature=0.0,
                max_tokens=2000,
                extra_body={"thinking": {"type": "disabled"}},
            )
        return await self.replace_document_graph(document_id, extraction)

    async def rebuild_space(
        self,
        space_slug: str,
        *,
        runtime: UserLLMRuntimeConfig | None = None,
    ) -> int:
        """Extract graphs for every active document in one space."""
        query: Any = text(
            """
            SELECT document.id
            FROM knowledge_documents AS document
            JOIN knowledge_spaces AS space ON space.id = document.space_id
            WHERE space.slug = :space_slug AND document.deleted_at IS NULL
            ORDER BY document.id
            """
        )
        async with database_service.session_factory() as session:
            document_ids = [
                int(row["id"])
                for row in (await session.exec(query, params={"space_slug": space_slug})).mappings().all()
            ]
        relations = 0
        for document_id in document_ids:
            relations += await self.extract_document(document_id, runtime=runtime)
        logger.info("knowledge_graph_space_rebuilt", document_count=len(document_ids), relation_count=relations)
        return relations

    async def search(
        self,
        entity_names: list[str],
        context: RetrievalContext,
        *,
        top_k: int,
        max_hops: int,
    ) -> list[KnowledgeHit]:
        """Traverse up to two hops using only relations whose evidence is authorized."""
        names = list(dict.fromkeys(name.casefold() for name in entity_names if name.strip()))[:10]
        if not names or max_hops < 1:
            return []
        hops = min(max_hops, 2)
        group_ids = list(context.group_ids)
        scoped_spaces = list(context.space_slugs) or None
        statement: Any = text(
            """
            WITH RECURSIVE accessible_relations AS (
                SELECT relation.*
                FROM knowledge_relations AS relation
                JOIN knowledge_documents AS document ON document.id = relation.document_id
                JOIN knowledge_spaces AS space ON space.id = relation.space_id
                WHERE document.deleted_at IS NULL
                  AND (
                      space.is_public
                      OR EXISTS (
                          SELECT 1 FROM knowledge_space_principals AS acl
                          WHERE acl.space_id = space.id AND (
                              (acl.principal_type = 'user' AND acl.principal_id = :user_id)
                              OR (acl.principal_type = 'group' AND acl.principal_id = ANY(CAST(:group_ids AS text[])))
                          )
                      )
                      OR EXISTS (
                          SELECT 1 FROM knowledge_document_principals AS acl
                          WHERE acl.document_id = document.id AND (
                              (acl.principal_type = 'user' AND acl.principal_id = :user_id)
                              OR (acl.principal_type = 'group' AND acl.principal_id = ANY(CAST(:group_ids AS text[])))
                          )
                      )
                  )
                  AND (CAST(:space_slugs AS text[]) IS NULL OR space.slug = ANY(CAST(:space_slugs AS text[])))
            ), matched_entities AS (
                SELECT DISTINCT entity.id
                FROM knowledge_entities AS entity
                JOIN accessible_relations AS relation
                  ON relation.source_entity_id = entity.id OR relation.target_entity_id = entity.id
                WHERE entity.normalized_name = ANY(CAST(:entity_names AS text[]))
                   OR entity.aliases ?| CAST(:entity_names AS text[])
            ), walk AS (
                SELECT relation.id, relation.source_entity_id, relation.target_entity_id,
                       relation.predicate, relation.document_id, relation.chunk_id,
                       relation.confidence,
                       CASE WHEN relation.source_entity_id = matched.id
                            THEN relation.target_entity_id ELSE relation.source_entity_id END AS frontier_id,
                       ARRAY[relation.id] AS path, 1 AS depth
                FROM accessible_relations AS relation
                JOIN matched_entities AS matched
                  ON relation.source_entity_id = matched.id OR relation.target_entity_id = matched.id
                UNION ALL
                SELECT relation.id, relation.source_entity_id, relation.target_entity_id,
                       relation.predicate, relation.document_id, relation.chunk_id,
                       relation.confidence,
                       CASE WHEN relation.source_entity_id = walk.frontier_id
                            THEN relation.target_entity_id ELSE relation.source_entity_id END,
                       walk.path || relation.id, walk.depth + 1
                FROM walk
                JOIN accessible_relations AS relation
                  ON relation.source_entity_id = walk.frontier_id OR relation.target_entity_id = walk.frontier_id
                WHERE walk.depth < :max_hops AND NOT relation.id = ANY(walk.path)
            )
            SELECT DISTINCT ON (walk.chunk_id)
                   walk.chunk_id, walk.document_id, chunk.content, document.source,
                   space.slug AS space_slug, document.metadata || chunk.metadata AS metadata,
                   source_entity.canonical_name AS source_entity,
                   target_entity.canonical_name AS target_entity,
                   walk.predicate, walk.confidence, walk.depth
            FROM walk
            JOIN knowledge_chunks AS chunk ON chunk.id = walk.chunk_id
            JOIN knowledge_documents AS document ON document.id = walk.document_id
            JOIN knowledge_spaces AS space ON space.id = document.space_id
            JOIN knowledge_entities AS source_entity ON source_entity.id = walk.source_entity_id
            JOIN knowledge_entities AS target_entity ON target_entity.id = walk.target_entity_id
            ORDER BY walk.chunk_id, walk.depth, walk.confidence DESC
            LIMIT :top_k
            """
        )
        params = {
            "user_id": context.user_id,
            "group_ids": group_ids,
            "space_slugs": scoped_spaces,
            "entity_names": names,
            "max_hops": hops,
            "top_k": min(max(1, top_k), 20),
        }
        with trace_span("knowledge_graph.search", entity_count=len(names), max_hops=hops) as span:
            async with database_service.session_factory() as session:
                rows = (await session.exec(statement, params=params)).mappings().all()
            span.set_attribute("hit_count", len(rows))
        return [
            KnowledgeHit(
                chunk_id=int(row["chunk_id"]),
                document_id=int(row["document_id"]),
                content=str(row["content"]),
                source=str(row["source"]),
                space_slug=str(row["space_slug"]),
                metadata={
                    **dict(row["metadata"] or {}),
                    "graph_relation": {
                        "source": str(row["source_entity"]),
                        "predicate": str(row["predicate"]),
                        "target": str(row["target_entity"]),
                        "depth": int(row["depth"]),
                    },
                },
                score=float(row["confidence"]) / int(row["depth"]),
                retrieval_scores={"graph": float(row["confidence"]) / int(row["depth"])},
            )
            for row in rows
        ]


knowledge_graph_service = KnowledgeGraphService()
