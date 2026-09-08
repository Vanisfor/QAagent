"""Tests for evidence contract normalization and deterministic fallback."""

import asyncio

from app.schemas.knowledge import KnowledgeHit
from app.schemas.retrieval import EvidenceAssessment, QueryPlan, RetrievalBundle
from app.core.langgraph.tools.knowledge_search import _render_evidence_result
from app.services.evidence_evaluator import EvidenceEvaluatorService


class FailingStructuredLLM:
    """Raise before returning a structured evidence assessment."""

    async def call(self, messages, *, response_format, **kwargs):
        """Simulate an unavailable or irreparably invalid evaluator response."""
        raise RuntimeError("invalid structured output")


def test_evidence_assessment_normalizes_known_json_mode_aliases() -> None:
    """The observed answer/queries shape maps to the canonical contract."""
    assessment = EvidenceAssessment.model_validate({"answer": None, "queries": ["rewritten query"]})

    assert assessment.sufficient is False
    assert assessment.reason_code == "missing_evidence"
    assert assessment.rewritten_queries == ["rewritten query"]


def test_evidence_assessment_alias_shape_fails_closed_without_answer() -> None:
    """An empty legacy response cannot claim evidence sufficiency implicitly."""
    assessment = EvidenceAssessment.model_validate({"answer": None, "queries": []})

    assert assessment.sufficient is False
    assert assessment.reason_code == "missing_evidence"


def test_evidence_evaluator_failure_does_not_treat_hits_as_sufficient() -> None:
    """Retrieved candidates survive without becoming an affirmative grade."""
    evaluator = EvidenceEvaluatorService(llm_factory=lambda runtime: FailingStructuredLLM())

    assessment = asyncio.run(
        evaluator.evaluate(
            "query",
            QueryPlan(queries=["query"]),
            [KnowledgeHit(content="evidence", source="doc.md")],
            runtime=object(),
        )
    )

    assert assessment == EvidenceAssessment(sufficient=False, reason_code="evaluation_failed")


def test_evidence_evaluator_fallback_rejects_missing_hits() -> None:
    """An invalid evaluator response cannot invent sufficiency without evidence."""
    evaluator = EvidenceEvaluatorService(llm_factory=lambda runtime: FailingStructuredLLM())

    assessment = asyncio.run(
        evaluator.evaluate(
            "query",
            QueryPlan(queries=["query"]),
            [],
            runtime=object(),
        )
    )

    assert assessment == EvidenceAssessment(sufficient=False, reason_code="evaluation_failed")


def test_failed_grade_warning_reaches_the_answering_model() -> None:
    """The tool keeps candidates but labels sufficiency as unconfirmed."""
    hit = KnowledgeHit(content="candidate evidence", source="doc.md")
    bundle = RetrievalBundle(
        plan=QueryPlan(queries=["query"]),
        hits=[hit],
        assessment=EvidenceAssessment(sufficient=False, reason_code="evaluation_failed"),
    )

    rendered = _render_evidence_result(bundle)

    assert "sufficiency to answer the question is unconfirmed" in rendered
    assert "candidate evidence" in rendered
