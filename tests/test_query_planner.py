"""Tests for explicit structured query planning."""

import asyncio

from app.schemas.knowledge import RetrievalContext
from app.schemas.retrieval import QueryPlan
from app.services.query_planner import QueryPlannerService


class FakeStructuredLLM:
    """Return a prebuilt structured plan or raise a configured error."""

    def __init__(self, response=None, error: Exception | None = None) -> None:
        """Store one response or error."""
        self.response = response
        self.error = error

    async def call(self, messages, *, response_format, **kwargs):
        """Return the configured structured response."""
        if self.error is not None:
            raise self.error
        return self.response


def test_planner_deduplicates_queries_and_cannot_expand_space_scope() -> None:
    """Model-generated filters may narrow but never expand trusted space scope."""
    llm = FakeStructuredLLM(
        QueryPlan(
            intent="research",
            queries=["deployment policy", "deployment policy", "rollback policy"],
            entity_names=["Deployment Service"],
            space_slugs=["product", "unauthorized"],
            use_graph=True,
            max_hops=2,
        )
    )
    planner = QueryPlannerService(llm_factory=lambda runtime: llm)

    plan = asyncio.run(
        planner.plan(
            "research deployment",
            RetrievalContext(user_id="7", space_slugs=("product",)),
            runtime=object(),
            requested_intent="research",
        )
    )

    assert plan.queries == ["deployment policy", "rollback policy"]
    assert plan.space_slugs == ["product"]
    assert plan.intent == "research"
    assert plan.use_graph is True


def test_planner_failure_uses_bounded_single_query_fallback() -> None:
    """Planner failure preserves retrieval availability without inventing filters."""
    planner = QueryPlannerService(llm_factory=lambda runtime: FakeStructuredLLM(error=RuntimeError("down")))

    plan = asyncio.run(
        planner.plan(
            "deployment policy",
            RetrievalContext(user_id="7", space_slugs=("product",)),
            runtime=object(),
            requested_intent="qa",
        )
    )

    assert plan.queries == ["deployment policy"]
    assert plan.space_slugs == ["product"]
    assert plan.use_graph is False
    assert plan.max_hops == 0
