"""Tests for knowledge-graph extraction and provenance contracts."""

from pydantic import ValidationError
from pytest import raises

from app.schemas.retrieval import GraphEntity, GraphExtraction, GraphRelation


def test_graph_relation_requires_known_entity_keys_and_chunk_provenance() -> None:
    """Extraction validation rejects relations without resolvable provenance."""
    extraction = GraphExtraction(
        entities=[
            GraphEntity(key="service", name="Deployment Service", entity_type="system"),
            GraphEntity(key="db", name="Production DB", entity_type="database"),
        ],
        relations=[
            GraphRelation(
                source_key="service",
                target_key="db",
                predicate="writes_to",
                chunk_index=2,
                confidence=0.9,
            )
        ],
    )

    assert extraction.relations[0].chunk_index == 2

    with raises(ValidationError, match="unknown entity"):
        GraphExtraction(
            entities=[GraphEntity(key="service", name="Service", entity_type="system")],
            relations=[
                GraphRelation(
                    source_key="service",
                    target_key="missing",
                    predicate="depends_on",
                    chunk_index=0,
                )
            ],
        )
