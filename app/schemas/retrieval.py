"""Structured query-planning, graph-extraction and retrieval contracts."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.knowledge import KnowledgeHit

RetrievalIntent = Literal["qa", "search", "research", "wiki"]


class QueryPlan(BaseModel):
    """Bounded structured plan produced before retrieval begins."""

    model_config = {"extra": "forbid"}

    intent: RetrievalIntent = "qa"
    queries: list[str] = Field(default_factory=list, max_length=5)
    entity_names: list[str] = Field(default_factory=list, max_length=10)
    space_slugs: list[str] = Field(default_factory=list, max_length=10)
    use_graph: bool = False
    max_hops: int = Field(default=0, ge=0, le=2)

    @field_validator("queries", "entity_names", "space_slugs")
    @classmethod
    def _strip_values(cls, values: list[str]) -> list[str]:
        """Strip empty values while preserving model-provided order."""
        return [value.strip() for value in values if value.strip()]


class GraphEntity(BaseModel):
    """Canonical entity extracted from one normalized document."""

    model_config = {"extra": "forbid"}

    key: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")
    name: str = Field(..., min_length=1, max_length=300)
    entity_type: str = Field(..., min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class GraphRelation(BaseModel):
    """Directed relation with mandatory source-chunk provenance."""

    model_config = {"extra": "forbid"}

    source_key: str = Field(..., min_length=1, max_length=100)
    target_key: str = Field(..., min_length=1, max_length=100)
    predicate: str = Field(..., min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.:-]+$")
    chunk_index: int = Field(..., ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class GraphExtraction(BaseModel):
    """Validated entity/relation payload generated for one document."""

    model_config = {"extra": "forbid"}

    entities: list[GraphEntity] = Field(default_factory=list, max_length=100)
    relations: list[GraphRelation] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _validate_relation_keys(self) -> "GraphExtraction":
        keys = {entity.key for entity in self.entities}
        for relation in self.relations:
            if relation.source_key not in keys or relation.target_key not in keys:
                raise ValueError("relation references an unknown entity key")
        return self


class RetrievalBundle(BaseModel):
    """Explicit plan plus its final ACL-filtered evidence shortlist."""

    plan: QueryPlan
    hits: list[KnowledgeHit]
