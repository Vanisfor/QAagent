"""Explicit Planner → Hybrid/Graph → fusion retrieval pipeline."""

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import KnowledgeHit, RetrievalContext
from app.schemas.retrieval import RetrievalBundle, RetrievalIntent
from app.services.knowledge import KnowledgeService, _reciprocal_rank_fusion, knowledge_service
from app.services.knowledge_graph import KnowledgeGraphService, knowledge_graph_service
from app.services.query_planner import QueryPlannerService, query_planner_service
from app.services.user_llm_settings import UserLLMRuntimeConfig


class ExplicitRetrievalPipeline:
    """Execute a bounded structured plan against hybrid and graph retrieval."""

    def __init__(
        self,
        planner: QueryPlannerService,
        knowledge: KnowledgeService,
        graph: KnowledgeGraphService,
    ) -> None:
        """Inject planning and retrieval services."""
        self._planner = planner
        self._knowledge = knowledge
        self._graph = graph

    async def retrieve(
        self,
        query: str,
        context: RetrievalContext,
        runtime: UserLLMRuntimeConfig | Any,
        *,
        intent: RetrievalIntent,
        top_k: int,
    ) -> RetrievalBundle:
        """Plan queries, execute them concurrently and fuse unique provenance chunks."""
        plan = await self._planner.plan(query, context, runtime=runtime, requested_intent=intent)
        effective_context = context.model_copy(update={"space_slugs": tuple(plan.space_slugs)})
        per_query_k = min(max(top_k, settings.KNOWLEDGE_TOP_K), 20)
        with trace_span("retrieval.execute_plan", query_count=len(plan.queries), use_graph=plan.use_graph) as span:
            rankings = await asyncio.gather(
                *(
                    self._knowledge.search(search_query, context=effective_context, top_k=per_query_k)
                    for search_query in plan.queries
                )
            )
            if plan.use_graph:
                graph_hits = await self._graph.search(
                    plan.entity_names,
                    effective_context,
                    top_k=per_query_k,
                    max_hops=plan.max_hops,
                )
                rankings.append(graph_hits)

            candidates: dict[int, KnowledgeHit] = {}
            id_rankings: list[list[int]] = []
            for ranking in rankings:
                ids: list[int] = []
                for hit in ranking:
                    ids.append(hit.chunk_id)
                    existing = candidates.get(hit.chunk_id)
                    if existing is None:
                        candidates[hit.chunk_id] = hit
                    else:
                        candidates[hit.chunk_id] = existing.model_copy(
                            update={
                                "retrieval_scores": {**existing.retrieval_scores, **hit.retrieval_scores},
                                "metadata": {**existing.metadata, **hit.metadata},
                            }
                        )
                id_rankings.append(ids)

            fused = _reciprocal_rank_fusion(id_rankings, rrf_k=settings.KNOWLEDGE_RRF_K)
            max_score = max(fused.values(), default=1.0)
            hits = [
                candidates[chunk_id].model_copy(
                    update={
                        "score": score / max_score,
                        "retrieval_scores": {**candidates[chunk_id].retrieval_scores, "plan_rrf": score},
                    }
                )
                for chunk_id, score in list(fused.items())[: min(max(1, top_k), 20)]
            ]
            span.set_attribute("hit_count", len(hits))
            span.set_attribute("ranking_count", len(rankings))
        logger.info(
            "explicit_retrieval_completed",
            intent=intent,
            query_count=len(plan.queries),
            hit_count=len(hits),
            graph_used=plan.use_graph,
        )
        return RetrievalBundle(plan=plan, hits=hits)


retrieval_pipeline = ExplicitRetrievalPipeline(query_planner_service, knowledge_service, knowledge_graph_service)
