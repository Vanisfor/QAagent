"""Structured evidence sufficiency evaluation for bounded agentic retrieval."""

from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.evidence import build_evidence_block
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import KnowledgeHit
from app.schemas.retrieval import EvidenceAssessment, QueryPlan
from app.services.llm import LLMService
from app.services.user_llm_settings import UserLLMRuntimeConfig

_EVALUATOR_PROMPT = """Decide whether the retrieved evidence is sufficient to answer the query.
Use only the supplied evidence. If insufficient, provide at most three standalone rewritten
search queries that target the missing facts. Do not answer the user and do not expose reasoning.
"""


class EvidenceEvaluatorService:
    """Return a structured sufficiency decision and bounded query rewrites."""

    def __init__(self, llm_factory: Callable[[Any], Any] | None = None) -> None:
        """Accept an injectable structured-output LLM factory."""
        self._llm_factory = llm_factory or (lambda runtime: LLMService(runtime))

    async def evaluate(
        self,
        query: str,
        plan: QueryPlan,
        hits: list[KnowledgeHit],
        *,
        runtime: UserLLMRuntimeConfig | Any,
    ) -> EvidenceAssessment:
        """Assess evidence and fail safely to a deterministic availability rule."""
        try:
            evidence = build_evidence_block(hits) if hits else "<evidence></evidence>"
            with trace_span("retrieval.evaluate", hit_count=len(hits)):
                assessment = await self._llm_factory(runtime).call(
                    [
                        SystemMessage(content=_EVALUATOR_PROMPT),
                        HumanMessage(
                            content=(
                                f"Intent: {plan.intent}\nQuery: {query[:2000]}\n"
                                f"Planned queries: {plan.queries}\n\n{evidence}"
                            )
                        ),
                    ],
                    response_format=EvidenceAssessment,
                    temperature=0.0,
                    max_tokens=600,
                    extra_body={"thinking": {"type": "disabled"}},
                )
            if assessment.sufficient:
                return assessment.model_copy(update={"reason_code": "sufficient", "rewritten_queries": []})
            return assessment
        except Exception as error:
            logger.exception("evidence_evaluation_failed", error_type=type(error).__name__)
            return EvidenceAssessment(
                sufficient=bool(hits),
                reason_code="sufficient" if hits else "missing_evidence",
                rewritten_queries=[],
            )


evidence_evaluator_service = EvidenceEvaluatorService()
