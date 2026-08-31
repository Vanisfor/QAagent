"""Evidence-backed Research and Wiki synthesis workflows."""

from collections.abc import Callable
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.evidence import build_evidence_block
from app.core.logging import logger
from app.core.tracing import trace_span
from app.schemas.knowledge import RetrievalContext
from app.schemas.retrieval import RetrievalBundle, RetrievalIntent
from app.schemas.workflows import (
    EvidenceSource,
    ResearchDraft,
    ResearchResponse,
    WikiDraft,
    WikiResponse,
)
from app.services.llm import LLMService
from app.services.user_llm_settings import UserLLMRuntimeConfig


class RetrievalPipeline(Protocol):
    """Retrieval boundary shared by chat, Research and Wiki."""

    async def retrieve(
        self,
        query: str,
        context: RetrievalContext,
        runtime: UserLLMRuntimeConfig | Any,
        *,
        intent: RetrievalIntent,
        top_k: int,
    ) -> RetrievalBundle:
        """Return an explicit plan and ACL-filtered evidence."""
        ...


class KnowledgeEvidenceNotFound(Exception):
    """Raised when a workflow has no authorized evidence to synthesize."""


class KnowledgeWorkflowService:
    """Generate stable Research and Wiki products from authorized evidence."""

    def __init__(self, pipeline: RetrievalPipeline, llm_factory: Callable[[Any], Any] | None = None) -> None:
        """Inject retrieval and structured synthesis boundaries."""
        self._pipeline = pipeline
        self._llm_factory = llm_factory or (lambda runtime: LLMService(runtime))

    async def research(
        self,
        query: str,
        context: RetrievalContext,
        *,
        runtime: UserLLMRuntimeConfig | Any,
    ) -> ResearchResponse:
        """Plan, retrieve and synthesize a citation-validated research report."""
        bundle = await self._pipeline.retrieve(query, context, runtime, intent="research", top_k=12)
        if not bundle.hits:
            raise KnowledgeEvidenceNotFound("no authorized evidence found for research")
        sources = self._sources(bundle)
        with trace_span("workflow.research", source_count=len(sources)):
            draft = await self._llm_factory(runtime).call(
                [
                    SystemMessage(
                        content=(
                            "Create a concise research report using only the supplied evidence. "
                            "Every finding must cite one or more valid numeric evidence IDs."
                        )
                    ),
                    HumanMessage(content=f"Question: {query[:2000]}\n\n{build_evidence_block(bundle.hits)}"),
                ],
                response_format=ResearchDraft,
                temperature=0.1,
                extra_body={"thinking": {"type": "disabled"}},
            )
        valid_ids = {source.id for source in sources}
        findings = [
            finding.model_copy(update={"citation_ids": self._valid_citations(finding.citation_ids, valid_ids)})
            for finding in draft.findings
        ]
        logger.info("research_workflow_completed", source_count=len(sources), finding_count=len(findings))
        return ResearchResponse(
            title=draft.title,
            executive_summary=draft.executive_summary,
            findings=findings,
            sources=sources,
            plan=bundle.plan,
        )

    async def wiki(
        self,
        query: str,
        context: RetrievalContext,
        *,
        runtime: UserLLMRuntimeConfig | Any,
    ) -> WikiResponse:
        """Plan, retrieve and synthesize a citation-validated Wiki page."""
        bundle = await self._pipeline.retrieve(query, context, runtime, intent="wiki", top_k=12)
        if not bundle.hits:
            raise KnowledgeEvidenceNotFound("no authorized evidence found for wiki generation")
        sources = self._sources(bundle)
        with trace_span("workflow.wiki", source_count=len(sources)):
            draft = await self._llm_factory(runtime).call(
                [
                    SystemMessage(
                        content=(
                            "Create a neutral Wiki page using only the supplied evidence. "
                            "Each section must cite valid numeric evidence IDs."
                        )
                    ),
                    HumanMessage(content=f"Topic: {query[:2000]}\n\n{build_evidence_block(bundle.hits)}"),
                ],
                response_format=WikiDraft,
                temperature=0.1,
                extra_body={"thinking": {"type": "disabled"}},
            )
        valid_ids = {source.id for source in sources}
        sections = [
            section.model_copy(update={"citation_ids": self._valid_citations(section.citation_ids, valid_ids)})
            for section in draft.sections
        ]
        logger.info("wiki_workflow_completed", source_count=len(sources), section_count=len(sections))
        return WikiResponse(
            title=draft.title,
            summary=draft.summary,
            sections=sections,
            sources=sources,
            plan=bundle.plan,
        )

    @staticmethod
    def _sources(bundle: RetrievalBundle) -> list[EvidenceSource]:
        """Build server-owned source cards whose IDs match evidence numbering."""
        return [
            EvidenceSource(
                id=index,
                source=hit.source,
                space_slug=hit.space_slug,
                document_id=hit.document_id,
                chunk_id=hit.chunk_id,
            )
            for index, hit in enumerate(bundle.hits, start=1)
        ]

    @staticmethod
    def _valid_citations(citation_ids: list[int], valid_ids: set[int]) -> list[int]:
        """Remove invented and duplicate citation IDs while preserving order."""
        return list(dict.fromkeys(citation_id for citation_id in citation_ids if citation_id in valid_ids))


from app.services.retrieval_pipeline import retrieval_pipeline  # noqa: E402

knowledge_workflow_service = KnowledgeWorkflowService(retrieval_pipeline)
