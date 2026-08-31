"""Schemas for the RAG knowledge base.

These models describe document chunks (what gets embedded and stored in
pgvector) and retrieval hits (what comes back from a similarity search).
"""

import json
from collections.abc import Mapping
from typing import (
    Any,
    Dict,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)


class DocumentChunk(BaseModel):
    """A single chunk of a knowledge-base document.

    Attributes:
        content: The chunk text (embedded and stored verbatim).
        source: Human-readable source identifier (file path or title).
        metadata: Optional JSON-serializable key/value pairs (e.g. page, section).
    """

    content: str = Field(..., description="The chunk text")
    source: str = Field(..., description="Source document path or title")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Optional metadata")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_json_serializable(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure metadata can be stored as JSONB (fails fast at model level)."""
        json.dumps(v, ensure_ascii=False)
        return v


class KnowledgeHit(BaseModel):
    """A retrieved knowledge-base passage.

    Attributes:
        content: The matched chunk text.
        source: Source document path or title.
        metadata: Metadata stored with the chunk.
        similarity: Cosine similarity (0..1) between the query and the chunk.
    """

    chunk_id: int = Field(default=0, description="Stable knowledge chunk identifier")
    document_id: int = Field(default=0, description="Owning knowledge document identifier")
    content: str = Field(..., description="The matched chunk text")
    source: str = Field(..., description="Source document path or title")
    space_slug: str = Field(default="default-public", description="Owning knowledge-space slug")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Stored metadata")
    similarity: float = Field(default=0.0, description="Cosine similarity score")
    score: float = Field(default=0.0, description="Final retrieval score used for ranking")
    retrieval_scores: Dict[str, float] = Field(
        default_factory=dict,
        description="Content-free component scores such as dense, lexical and RRF",
    )


class RetrievalContext(BaseModel):
    """Server-authenticated principals and optional knowledge-space scope."""

    user_id: str = Field(..., min_length=1)
    group_ids: tuple[str, ...] = ()
    space_slugs: tuple[str, ...] = ()

    @property
    def principals(self) -> tuple[tuple[str, str], ...]:
        """Return additive ACL principals for the authenticated request."""
        return (("user", self.user_id),) + tuple(("group", group_id) for group_id in self.group_ids)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "RetrievalContext":
        """Build a retrieval context only from trusted RunnableConfig metadata."""
        metadata = config.get("metadata") or {}
        user_id = metadata.get("user_id")
        if user_id is None or not str(user_id).strip():
            raise ValueError("authenticated user context is required for knowledge retrieval")

        group_ids = tuple(str(value) for value in metadata.get("knowledge_group_ids", ()) if str(value).strip())
        space_slugs = tuple(str(value) for value in metadata.get("knowledge_space_slugs", ()) if str(value).strip())
        return cls(user_id=str(user_id), group_ids=group_ids, space_slugs=space_slugs)
