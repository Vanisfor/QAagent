"""Tests for Research and Wiki workflow synthesis."""

import asyncio

from app.schemas.knowledge import KnowledgeHit, RetrievalContext
from app.schemas.retrieval import QueryPlan, RetrievalBundle
from app.schemas.workflows import ResearchDraft, ResearchFinding, WikiDraft, WikiSection
from app.services.knowledge_workflows import KnowledgeWorkflowService


class FakePipeline:
    """Return a stable evidence bundle for workflow tests."""

    async def retrieve(self, query, context, runtime, *, intent, top_k):
        """Return one evidence item and the requested explicit plan."""
        return RetrievalBundle(
            plan=QueryPlan(intent=intent, queries=[query], space_slugs=list(context.space_slugs)),
            hits=[
                KnowledgeHit(
                    chunk_id=11,
                    document_id=2,
                    content="Deployment requires approval.",
                    source="policy.md",
                    space_slug="product",
                    score=0.9,
                )
            ],
        )


class FakeWorkflowLLM:
    """Return structured Research or Wiki drafts based on the requested schema."""

    async def call(self, messages, *, response_format, **kwargs):
        """Return the structured draft matching the requested schema."""
        if response_format is ResearchDraft:
            return ResearchDraft(
                title="Deployment research",
                executive_summary="Approval is required.",
                findings=[
                    ResearchFinding(
                        heading="Approval",
                        analysis="The policy requires approval.",
                        citation_ids=[1, 999],
                    )
                ],
            )
        return WikiDraft(
            title="Deployment",
            summary="Deployment policy overview.",
            sections=[WikiSection(heading="Approval", content="Approval is required.", citation_ids=[1, 999])],
        )


def test_research_workflow_returns_only_valid_evidence_citations() -> None:
    """Research output removes citation IDs not present in the evidence bundle."""
    service = KnowledgeWorkflowService(FakePipeline(), llm_factory=lambda runtime: FakeWorkflowLLM())

    response = asyncio.run(
        service.research(
            "deployment approval",
            RetrievalContext(user_id="7", space_slugs=("product",)),
            runtime=object(),
        )
    )

    assert response.findings[0].citation_ids == [1]
    assert response.sources[0].source == "policy.md"
    assert response.plan.intent == "research"


def test_wiki_workflow_returns_scoped_page_with_sources() -> None:
    """Wiki generation remains scoped and produces a fixed source card list."""
    service = KnowledgeWorkflowService(FakePipeline(), llm_factory=lambda runtime: FakeWorkflowLLM())

    response = asyncio.run(
        service.wiki(
            "deployment",
            RetrievalContext(user_id="7", space_slugs=("product",)),
            runtime=object(),
        )
    )

    assert response.sections[0].citation_ids == [1]
    assert response.sources[0].space_slug == "product"
    assert response.plan.intent == "wiki"
