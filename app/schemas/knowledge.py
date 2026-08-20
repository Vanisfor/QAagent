"""Schemas for the RAG knowledge base.

These models describe document chunks (what gets embedded and stored in
pgvector) and retrieval hits (what comes back from a similarity search).
"""

import json
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

    content: str = Field(..., description="The matched chunk text")
    source: str = Field(..., description="Source document path or title")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Stored metadata")
    similarity: float = Field(default=0.0, description="Cosine similarity score")
