"""Explicit LangGraph workflow for bounded agentic knowledge retrieval."""

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import KnowledgeHit, RetrievalContext
from app.schemas.retrieval import EvidenceAssessment, QueryPlan, RetrievalBundle, RetrievalIntent
from app.services.retrieval_pipeline import ExplicitRetrievalPipeline, retrieval_pipeline


@dataclass(frozen=True)
class AgenticRAGContext:
    """Request-only dependencies that must not be persisted in graph state."""

    retrieval: RetrievalContext
    llm_runtime: Any
    stream_writer: Callable[[dict[str, Any]], None] | None


class AgenticRAGState(TypedDict):
    """Serializable state passed between the explicit retrieval nodes."""

    original_query: str
    intent: RetrievalIntent
    top_k: int
    plan: QueryPlan | None
    pending_queries: list[str]
    executed_queries: list[str]
    rankings: list[list[KnowledgeHit]]
    hits: list[KnowledgeHit]
    assessment: EvidenceAssessment | None
    iterations: int


class AgenticRAGWorkflow:
    """Plan, retrieve, grade and rewrite through visible LangGraph nodes."""

    def __init__(self, pipeline: ExplicitRetrievalPipeline, *, max_loops: int) -> None:
        """Compile the workflow once with injected retrieval operations."""
        self._pipeline = pipeline
        self._max_loops = min(max(1, max_loops), 3)
        self.graph = self._build_graph()

    def _build_graph(
        self,
    ) -> CompiledStateGraph[AgenticRAGState, AgenticRAGContext, AgenticRAGState, AgenticRAGState]:
        """Build the standard bounded Agentic RAG topology."""
        builder = StateGraph(AgenticRAGState, context_schema=AgenticRAGContext)
        builder.add_node("plan_query", self._plan_query)
        builder.add_node("retrieve_evidence", self._retrieve_evidence)
        builder.add_node("grade_evidence", self._grade_evidence)
        builder.add_node("rewrite_query", self._rewrite_query)
        builder.add_node("return_evidence", self._return_evidence)

        builder.add_edge(START, "plan_query")
        builder.add_edge("plan_query", "retrieve_evidence")
        builder.add_edge("retrieve_evidence", "grade_evidence")
        builder.add_conditional_edges(
            "grade_evidence",
            self._route_after_grade,
            {
                "rewrite_query": "rewrite_query",
                "return_evidence": "return_evidence",
            },
        )
        builder.add_conditional_edges(
            "rewrite_query",
            self._route_after_rewrite,
            {
                "retrieve_evidence": "retrieve_evidence",
                "return_evidence": "return_evidence",
            },
        )
        builder.add_edge("return_evidence", END)
        return builder.compile(name="Agentic RAG")

    async def _plan_query(
        self,
        state: AgenticRAGState,
        runtime: Runtime[AgenticRAGContext],
    ) -> dict[str, Any]:
        """Create a bounded, ACL-normalized search plan."""
        plan = await self._pipeline.plan(
            state["original_query"],
            runtime.context.retrieval,
            runtime.context.llm_runtime,
            intent=state["intent"],
        )
        writer = runtime.context.stream_writer
        if writer is not None:
            writer(
                {
                    "type": "rag_plan",
                    "data": {"queries": plan.queries, "use_graph": plan.use_graph},
                }
            )
        return {"plan": plan, "pending_queries": plan.queries}

    async def _retrieve_evidence(
        self,
        state: AgenticRAGState,
        runtime: Runtime[AgenticRAGContext],
    ) -> dict[str, Any]:
        """Execute one hybrid/graph retrieval round."""
        plan = state["plan"]
        if plan is None:
            raise RuntimeError("retrieval plan is required before evidence retrieval")

        pending_queries = state["pending_queries"]
        iteration = state["iterations"] + 1
        round_rankings = await self._pipeline.search(
            plan,
            pending_queries,
            runtime.context.retrieval,
            top_k=state["top_k"],
            include_graph=iteration == 1,
        )
        rankings = [*state["rankings"], *round_rankings]
        hits = self._pipeline.fuse(rankings, top_k=state["top_k"])
        return {
            "rankings": rankings,
            "hits": hits,
            "iterations": iteration,
            "executed_queries": list(dict.fromkeys([*state["executed_queries"], *pending_queries])),
        }

    async def _grade_evidence(
        self,
        state: AgenticRAGState,
        runtime: Runtime[AgenticRAGContext],
    ) -> dict[str, Any]:
        """Grade evidence sufficiency before generation can continue."""
        plan = state["plan"]
        if plan is None:
            raise RuntimeError("retrieval plan is required before evidence grading")
        assessment = await self._pipeline.grade(
            state["original_query"],
            plan,
            state["rankings"],
            runtime.context.llm_runtime,
        )
        writer = runtime.context.stream_writer
        if writer is not None:
            writer(
                {
                    "type": "rag_evaluate",
                    "data": {
                        "sufficient": assessment.sufficient,
                        "reason_code": assessment.reason_code,
                        "rewritten_queries": assessment.rewritten_queries,
                        "iteration": state["iterations"],
                    },
                }
            )
        return {"assessment": assessment}

    def _route_after_grade(self, state: AgenticRAGState) -> Literal["rewrite_query", "return_evidence"]:
        """Continue only when a bounded rewrite can add new evidence."""
        assessment = state["assessment"]
        if assessment is None:
            raise RuntimeError("evidence assessment is required before routing")
        if assessment.sufficient or state["iterations"] >= self._max_loops:
            return "return_evidence"
        if not assessment.rewritten_queries:
            return "return_evidence"
        return "rewrite_query"

    @staticmethod
    def _rewrite_query(state: AgenticRAGState) -> dict[str, Any]:
        """Keep only new evaluator rewrites and extend the visible plan."""
        plan = state["plan"]
        assessment = state["assessment"]
        if plan is None or assessment is None:
            raise RuntimeError("plan and assessment are required before query rewriting")
        executed = set(state["executed_queries"])
        pending = [query for query in assessment.rewritten_queries if query not in executed][:3]
        updated_queries = list(dict.fromkeys([*plan.queries, *pending]))
        return {
            "plan": plan.model_copy(update={"queries": updated_queries}),
            "pending_queries": pending,
        }

    @staticmethod
    def _route_after_rewrite(state: AgenticRAGState) -> Literal["retrieve_evidence", "return_evidence"]:
        """Stop when the evaluator only repeated already-executed queries."""
        return "retrieve_evidence" if state["pending_queries"] else "return_evidence"

    @staticmethod
    def _return_evidence(state: AgenticRAGState) -> dict[str, Any]:
        """Expose the final fused shortlist to the calling knowledge tool."""
        logger.info(
            "agentic_rag_completed",
            intent=state["intent"],
            query_count=len(state["plan"].queries) if state["plan"] else 0,
            hit_count=len(state["hits"]),
            iterations=state["iterations"],
        )
        return {}

    async def run(
        self,
        query: str,
        context: RetrievalContext,
        llm_runtime: Any,
        *,
        intent: RetrievalIntent,
        top_k: int,
        config: RunnableConfig | None = None,
    ) -> RetrievalBundle:
        """Invoke the compiled graph and return its typed retrieval result."""
        parent_writer = _stream_writer()
        with trace_span("retrieval.agentic_graph", intent=intent, top_k=top_k) as span:
            result = await self.graph.ainvoke(
                {
                    "original_query": query,
                    "intent": intent,
                    "top_k": min(max(1, top_k), 20),
                    "plan": None,
                    "pending_queries": [],
                    "executed_queries": [],
                    "rankings": [],
                    "hits": [],
                    "assessment": None,
                    "iterations": 0,
                },
                config=config,
                context=AgenticRAGContext(
                    retrieval=context,
                    llm_runtime=llm_runtime,
                    stream_writer=parent_writer,
                ),
            )
            span.set_attribute("hit_count", len(result.get("hits", [])))
            span.set_attribute("iterations", result.get("iterations", 0))
        plan = result.get("plan")
        assessment = result.get("assessment")
        if not isinstance(plan, QueryPlan) or not isinstance(assessment, EvidenceAssessment):
            raise RuntimeError("agentic RAG graph returned an incomplete result")
        return RetrievalBundle(
            plan=plan,
            hits=result.get("hits", []),
            assessment=assessment,
            iterations=result.get("iterations", 1),
        )


def _stream_writer() -> Any | None:
    """Return the current custom-stream writer when invoked inside LangGraph."""
    try:
        return get_stream_writer()
    except RuntimeError:
        return None


agentic_rag_workflow = AgenticRAGWorkflow(
    retrieval_pipeline,
    max_loops=settings.RETRIEVAL_MAX_LOOPS,
)
