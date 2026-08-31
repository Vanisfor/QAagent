"""Tests for evidence sufficiency and bounded rewrite-retrieve loops."""

import asyncio

from app.schemas.knowledge import KnowledgeHit, RetrievalContext
from app.schemas.retrieval import EvidenceAssessment, QueryPlan
from app.services.retrieval_pipeline import ExplicitRetrievalPipeline


class FakePlanner:
    """Return one fixed initial query."""

    async def plan(self, query, context, *, runtime, requested_intent):
        """Build a minimal plan."""
        return QueryPlan(intent=requested_intent, queries=["initial query"])


class FakeKnowledge:
    """Return evidence only for the rewritten query."""

    def __init__(self) -> None:
        """Track executed queries."""
        self.queries: list[str] = []

    async def search(self, query, *, context, top_k):
        """Return a hit for the evaluator rewrite."""
        self.queries.append(query)
        if query == "rewritten query":
            return [KnowledgeHit(chunk_id=7, document_id=3, content="evidence", source="doc.md", score=0.8)]
        return []


class FakeGraph:
    """Return no graph evidence."""

    async def search(self, entity_names, context, *, top_k, max_hops):
        """Return an empty graph ranking."""
        return []


class FakeEvaluator:
    """Request one rewrite and then mark evidence sufficient."""

    def __init__(self) -> None:
        """Track evaluation rounds."""
        self.calls = 0

    async def evaluate(self, query, plan, hits, *, runtime):
        """Return a rewrite until a hit exists."""
        self.calls += 1
        if hits:
            return EvidenceAssessment(sufficient=True, reason_code="sufficient")
        return EvidenceAssessment(
            sufficient=False,
            reason_code="missing_evidence",
            rewritten_queries=["rewritten query"],
        )


def test_pipeline_rewrites_once_and_stops_when_evidence_is_sufficient() -> None:
    """An insufficient first pass triggers one explicit bounded retrieval loop."""
    knowledge = FakeKnowledge()
    evaluator = FakeEvaluator()
    pipeline = ExplicitRetrievalPipeline(FakePlanner(), knowledge, FakeGraph(), evaluator, max_loops=2)  # type: ignore[arg-type]

    bundle = asyncio.run(
        pipeline.retrieve(
            "original question",
            RetrievalContext(user_id="7"),
            object(),
            intent="research",
            top_k=5,
        )
    )

    assert knowledge.queries == ["initial query", "rewritten query"]
    assert evaluator.calls == 2
    assert bundle.iterations == 2
    assert bundle.assessment.sufficient is True
    assert [hit.chunk_id for hit in bundle.hits] == [7]
