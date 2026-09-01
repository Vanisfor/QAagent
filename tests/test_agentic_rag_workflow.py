"""Tests for the explicit LangGraph Agentic RAG topology and loop."""

import asyncio
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.core.langgraph.rag_workflow import AgenticRAGWorkflow
from app.schemas.knowledge import KnowledgeHit, RetrievalContext
from app.schemas.retrieval import EvidenceAssessment, QueryPlan


class FakePipeline:
    """Provide deterministic node-level retrieval operations."""

    def __init__(self) -> None:
        """Track the query batches executed by the graph."""
        self.query_batches: list[list[str]] = []

    async def plan(self, query, context, runtime, *, intent):
        """Return one initial query."""
        return QueryPlan(intent=intent, queries=["initial query"])

    async def search(self, plan, queries, context, *, top_k, include_graph):
        """Return evidence only after the rewrite node runs."""
        self.query_batches.append(queries)
        if queries == ["rewritten query"]:
            return [[KnowledgeHit(chunk_id=7, document_id=3, content="evidence", source="doc.md")]]
        return [[]]

    async def grade(self, query, plan, rankings, runtime):
        """Request one rewrite, then accept the retrieved evidence."""
        if any(ranking for ranking in rankings):
            return EvidenceAssessment(sufficient=True, reason_code="sufficient")
        return EvidenceAssessment(
            sufficient=False,
            reason_code="missing_evidence",
            rewritten_queries=["rewritten query"],
        )

    @staticmethod
    def fuse(rankings, *, top_k):
        """Return unique hits in retrieval order for this focused graph test."""
        hits = [hit for ranking in rankings for hit in ranking]
        return list({hit.chunk_id: hit for hit in hits}.values())[:top_k]


class DuplicateRewritePipeline(FakePipeline):
    """Return only an already-executed rewrite."""

    async def grade(self, query, plan, rankings, runtime):
        """Keep evidence insufficient without providing a useful next query."""
        return EvidenceAssessment(
            sufficient=False,
            reason_code="missing_evidence",
            rewritten_queries=["initial query"],
        )


class OuterState(TypedDict):
    """Minimal parent graph state for custom-stream propagation testing."""

    done: bool


def test_agentic_rag_graph_exposes_standard_nodes_and_loop() -> None:
    """The compiled graph makes planning, grading and rewriting inspectable."""
    workflow = AgenticRAGWorkflow(FakePipeline(), max_loops=2)  # type: ignore[arg-type]
    drawable = workflow.graph.get_graph()

    assert {"plan_query", "retrieve_evidence", "grade_evidence", "rewrite_query", "return_evidence"} <= set(
        drawable.nodes
    )
    edges = {(edge.source, edge.target) for edge in drawable.edges}
    assert ("plan_query", "retrieve_evidence") in edges
    assert ("retrieve_evidence", "grade_evidence") in edges
    assert ("grade_evidence", "rewrite_query") in edges
    assert ("rewrite_query", "retrieve_evidence") in edges
    assert ("grade_evidence", "return_evidence") in edges


def test_agentic_rag_graph_rewrites_then_returns_sufficient_evidence() -> None:
    """Insufficient evidence takes one bounded graph loop before returning."""
    pipeline = FakePipeline()
    workflow = AgenticRAGWorkflow(pipeline, max_loops=2)  # type: ignore[arg-type]

    bundle = asyncio.run(
        workflow.run(
            "original question",
            RetrievalContext(user_id="7"),
            object(),
            intent="qa",
            top_k=5,
        )
    )

    assert pipeline.query_batches == [["initial query"], ["rewritten query"]]
    assert bundle.iterations == 2
    assert bundle.assessment.sufficient is True
    assert bundle.plan.queries == ["initial query", "rewritten query"]
    assert [hit.chunk_id for hit in bundle.hits] == [7]


def test_agentic_rag_events_propagate_through_parent_graph() -> None:
    """Plan and grade events remain visible on the existing SSE custom stream."""
    workflow = AgenticRAGWorkflow(FakePipeline(), max_loops=2)  # type: ignore[arg-type]

    async def run_rag(_: OuterState, config: RunnableConfig) -> dict[str, bool]:
        await workflow.run(
            "original question",
            RetrievalContext(user_id="7"),
            object(),
            intent="qa",
            top_k=5,
            config=config,
        )
        return {"done": True}

    builder = StateGraph(OuterState)
    builder.add_node("knowledge_search", run_rag)
    builder.add_edge(START, "knowledge_search")
    builder.add_edge("knowledge_search", END)
    parent = builder.compile()

    async def collect_events() -> list[dict]:
        return [event async for event in parent.astream({"done": False}, stream_mode="custom")]

    events = asyncio.run(collect_events())
    event_types = [event["type"] for event in events]
    assert event_types == ["rag_plan", "rag_evaluate", "rag_evaluate"]


def test_agentic_rag_graph_stops_on_duplicate_rewrite() -> None:
    """A repeated evaluator query cannot create an unbounded graph cycle."""
    pipeline = DuplicateRewritePipeline()
    workflow = AgenticRAGWorkflow(pipeline, max_loops=3)  # type: ignore[arg-type]

    bundle = asyncio.run(
        workflow.run(
            "original question",
            RetrievalContext(user_id="7"),
            object(),
            intent="qa",
            top_k=5,
        )
    )

    assert pipeline.query_batches == [["initial query"]]
    assert bundle.iterations == 1
    assert bundle.assessment.sufficient is False
