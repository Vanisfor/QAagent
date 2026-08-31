"""Stable request and response schemas for Research and Wiki workflows."""

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import BaseResponse
from app.schemas.retrieval import QueryPlan


class KnowledgeWorkflowRequest(BaseModel):
    """Authenticated workflow request with an optional narrowing space scope."""

    query: str = Field(..., min_length=2, max_length=2000)
    space_slugs: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("space_slugs")
    @classmethod
    def _normalize_spaces(cls, values: list[str]) -> list[str]:
        """Remove empty and duplicate space slugs without changing order."""
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class EvidenceSource(BaseModel):
    """Fixed source card corresponding to one evidence citation ID."""

    id: int
    source: str
    space_slug: str
    document_id: int
    chunk_id: int


class ResearchFinding(BaseModel):
    """One evidence-backed research finding."""

    heading: str = Field(..., min_length=1, max_length=300)
    analysis: str = Field(..., min_length=1, max_length=8000)
    citation_ids: list[int] = Field(default_factory=list, max_length=20)


class ResearchDraft(BaseModel):
    """LLM-generated portion of a research report before citation validation."""

    title: str = Field(..., min_length=1, max_length=300)
    executive_summary: str = Field(..., min_length=1, max_length=8000)
    findings: list[ResearchFinding] = Field(default_factory=list, max_length=20)


class ResearchResponse(BaseResponse):
    """Validated research report with server-owned plan and source cards."""

    title: str
    executive_summary: str
    findings: list[ResearchFinding]
    sources: list[EvidenceSource]
    plan: QueryPlan


class WikiSection(BaseModel):
    """One generated Wiki section with validated evidence citations."""

    heading: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., min_length=1, max_length=12000)
    citation_ids: list[int] = Field(default_factory=list, max_length=30)


class WikiDraft(BaseModel):
    """LLM-generated Wiki content before citation validation."""

    title: str = Field(..., min_length=1, max_length=300)
    summary: str = Field(..., min_length=1, max_length=8000)
    sections: list[WikiSection] = Field(default_factory=list, max_length=30)


class WikiResponse(BaseResponse):
    """Validated Wiki page with server-owned plan and source cards."""

    title: str
    summary: str
    sections: list[WikiSection]
    sources: list[EvidenceSource]
    plan: QueryPlan
