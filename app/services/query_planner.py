"""Explicit structured query planning before hybrid and graph retrieval."""

from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import RetrievalContext
from app.schemas.retrieval import QueryPlan, RetrievalIntent
from app.services.llm import LLMService
from app.services.user_llm_settings import UserLLMRuntimeConfig

_PLANNER_PROMPT = """You are a retrieval query planner.
Return a small, explicit plan for the requested intent.
- Generate one to five standalone search queries.
- Extract only named entities useful for a provenance-backed knowledge graph.
- Space filters may only use values from the allowed-space list.
- use_graph only for entity relationships or multi-hop questions.
- max_hops must be 0, 1, or 2.
Do not answer the question and do not include credentials or reasoning.
"""


class QueryPlannerService:
    """Generate and security-normalize one bounded retrieval plan."""

    def __init__(self, llm_factory: Callable[[Any], Any] | None = None) -> None:
        """Accept an injectable structured-output LLM factory."""
        self._llm_factory = llm_factory or (lambda runtime: LLMService(runtime))

    async def plan(
        self,
        query: str,
        context: RetrievalContext,
        *,
        runtime: UserLLMRuntimeConfig | Any,
        requested_intent: RetrievalIntent,
    ) -> QueryPlan:
        """Generate a plan and constrain all filters to trusted request scope."""
        allowed_spaces = list(context.space_slugs)
        try:
            llm = self._llm_factory(runtime)
            with trace_span("retrieval.plan", requested_intent=requested_intent) as span:
                plan = await llm.call(
                    [
                        SystemMessage(content=_PLANNER_PROMPT),
                        HumanMessage(
                            content=(
                                f"Requested intent: {requested_intent}\n"
                                f"Allowed spaces: {allowed_spaces or ['all accessible spaces']}\n"
                                f"User query: {query[:2000]}"
                            )
                        ),
                    ],
                    response_format=QueryPlan,
                    temperature=0.0,
                    max_tokens=800,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                normalized = self._normalize(plan, query, allowed_spaces, requested_intent)
                span.set_attribute("query_count", len(normalized.queries))
                span.set_attribute("entity_count", len(normalized.entity_names))
                span.set_attribute("use_graph", normalized.use_graph)
                return normalized
        except Exception as error:
            logger.exception("retrieval_planning_failed", error_type=type(error).__name__)
            return QueryPlan(
                intent=requested_intent,
                queries=[query.strip()[:2000]],
                space_slugs=allowed_spaces,
                use_graph=False,
                max_hops=0,
            )

    @staticmethod
    def _normalize(
        plan: QueryPlan,
        original_query: str,
        allowed_spaces: list[str],
        requested_intent: RetrievalIntent,
    ) -> QueryPlan:
        """Deduplicate values and ensure model output cannot expand authorization."""
        queries = list(dict.fromkeys(plan.queries))[:5] or [original_query.strip()[:2000]]
        entities = list(dict.fromkeys(plan.entity_names))[:10]
        if allowed_spaces:
            spaces = [space for space in dict.fromkeys(plan.space_slugs) if space in allowed_spaces]
            spaces = spaces or allowed_spaces
        else:
            spaces = list(dict.fromkeys(plan.space_slugs))[:10]
        use_graph = plan.use_graph and bool(entities)
        return plan.model_copy(
            update={
                "intent": requested_intent,
                "queries": queries,
                "entity_names": entities,
                "space_slugs": spaces,
                "use_graph": use_graph,
                "max_hops": plan.max_hops if use_graph else 0,
            }
        )


query_planner_service = QueryPlannerService()
