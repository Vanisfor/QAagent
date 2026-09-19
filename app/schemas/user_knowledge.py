"""Owner-scoped personal knowledge API contracts."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class KnowledgeSpaceCreate(BaseModel):
    """Create one private space; identity and visibility are server-owned."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9-]*$")

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        """Reject presentation-only whitespace."""
        return value.strip()


class KnowledgeSpaceSummary(BaseModel):
    """Safe current-user view of an accessible knowledge space."""

    slug: str
    name: str
    role: str
    is_public: bool
    document_count: int = 0


class KnowledgeDocumentSummary(BaseModel):
    """Safe document metadata without storage paths or ACL internals."""

    id: int
    source: str
    title: str
    status: str
    updated_at: datetime


class KnowledgeIngestionJobView(BaseModel):
    """User-visible asynchronous ingestion state."""

    id: int
    space_slug: str
    file_name: str
    status: str
    attempts: int
    document_id: int | None = None
    error: str | None = None
    created_at: datetime


class KnowledgeUploadResponse(BaseModel):
    """Receipt returned after a validated upload is durably queued."""

    job: KnowledgeIngestionJobView

