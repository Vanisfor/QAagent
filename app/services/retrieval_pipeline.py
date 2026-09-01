"""Reusable planning, retrieval, grading and fusion operations for RAG workflows."""

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import KnowledgeHit, RetrievalContext
from app.schemas.retrieval import EvidenceAssessment, QueryPlan, RetrievalBundle, RetrievalIntent
from app.services.evidence_evaluator import EvidenceEvaluatorService, evidence_evaluator_service
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
        evaluator: EvidenceEvaluatorService,
        max_loops: int = 2,
    ) -> None:
        """Inject planning and retrieval services."""
        self._planner = planner
        self._knowledge = knowledge
        self._graph = graph
        self._evaluator = evaluator
        self._max_loops = min(max(1, max_loops), 3)

    async def retrieve(
        self,
        query: str,
        context: RetrievalContext,
        runtime: UserLLMRuntimeConfig | Any,
        *,
        intent: RetrievalIntent,
        top_k: int,
    ) -> RetrievalBundle:
        """Run the same bounded workflow for non-LangGraph callers."""
        plan = await self.plan(query, context, runtime, intent=intent)
        with trace_span("retrieval.execute_plan", query_count=len(plan.queries), use_graph=plan.use_graph) as span:
            rankings: list[list[KnowledgeHit]] = []
            pending_queries = plan.queries
            executed_queries: set[str] = set()
            iterations = 0
            assessment = None
            while pending_queries and iterations < self._max_loops:
                iterations += 1
                executed_queries.update(pending_queries)
                round_rankings = await self.search(
                    plan,
                    pending_queries,
                    context,
                    top_k=top_k,
                    include_graph=iterations == 1,
                )
                rankings.extend(round_rankings)
                assessment = await self.grade(query, plan, rankings, runtime)
                if assessment.sufficient:
                    break
                pending_queries = [
                    rewrite for rewrite in assessment.rewritten_queries if rewrite not in executed_queries
                ][:3]
                plan = plan.model_copy(update={"queries": list(dict.fromkeys([*plan.queries, *pending_queries]))})
            hits = self.fuse(rankings, top_k=top_k)
            span.set_attribute("hit_count", len(hits))
            span.set_attribute("ranking_count", len(rankings))
            span.set_attribute("iterations", iterations)
        logger.info(
            "explicit_retrieval_completed",
            intent=intent,
            query_count=len(plan.queries),
            hit_count=len(hits),
            graph_used=plan.use_graph,
        )
        if assessment is None:
            assessment = await self.grade(query, plan, [hits], runtime)
        return RetrievalBundle(plan=plan, hits=hits, assessment=assessment, iterations=iterations or 1)

    async def plan(
        self,
        query: str,
        context: RetrievalContext,
        runtime: UserLLMRuntimeConfig | Any,
        *,
        intent: RetrievalIntent,
    ) -> QueryPlan:
        """Create one security-normalized retrieval plan."""
        return await self._planner.plan(query, context, runtime=runtime, requested_intent=intent)

    async def search(
        self,
        plan: QueryPlan,
        queries: list[str],
        context: RetrievalContext,
        *,
        top_k: int,
        include_graph: bool,
    ) -> list[list[KnowledgeHit]]:
        """Execute one retrieval round without deciding whether to loop."""
        effective_context = context.model_copy(update={"space_slugs": tuple(plan.space_slugs)})
        per_query_k = min(max(top_k, settings.KNOWLEDGE_TOP_K), 20)
        rankings: list[list[KnowledgeHit]] = []
        if include_graph and plan.use_graph:
            rankings.append(
                await self._graph.search(
                    plan.entity_names,
                    effective_context,
                    top_k=per_query_k,
                    max_hops=plan.max_hops,
                )
            )
        rankings.extend(
            await asyncio.gather(
                *(
                    self._knowledge.search(search_query, context=effective_context, top_k=per_query_k)
                    for search_query in queries
                )
            )
        )
        return rankings

    async def grade(
        self,
        query: str,
        plan: QueryPlan,
        rankings: list[list[KnowledgeHit]],
        runtime: UserLLMRuntimeConfig | Any,
    ) -> EvidenceAssessment:
        """Assess whether all evidence collected so far is sufficient."""
        hits = [hit for ranking in rankings for hit in ranking]
        return await self._evaluator.evaluate(query, plan, hits, runtime=runtime)

    @staticmethod
    def fuse(rankings: list[list[KnowledgeHit]], *, top_k: int) -> list[KnowledgeHit]:
        """Fuse ranked results while preserving provenance and component scores."""
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
        return [
            candidates[chunk_id].model_copy(
                update={
                    "score": score / max_score,
                    "retrieval_scores": {**candidates[chunk_id].retrieval_scores, "plan_rrf": score},
                }
            )
            for chunk_id, score in list(fused.items())[: min(max(1, top_k), 20)]
        ]


retrieval_pipeline = ExplicitRetrievalPipeline(
    query_planner_service,
    knowledge_service,
    knowledge_graph_service,
    evidence_evaluator_service,
    max_loops=settings.RETRIEVAL_MAX_LOOPS,
)
