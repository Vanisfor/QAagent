"""Add provenance-backed knowledge graph entities and relations.

Revision ID: a13f8d4c7e52
Revises: f27c6e9a4b31
Create Date: 2026-08-31 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a13f8d4c7e52"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "f27c6e9a4b31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create space-scoped entities and document/chunk-backed relations."""
    op.execute(
        """
        CREATE TABLE knowledge_entities (
            id BIGSERIAL PRIMARY KEY,
            space_id BIGINT NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
            canonical_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (space_id, entity_type, normalized_name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_relations (
            id BIGSERIAL PRIMARY KEY,
            space_id BIGINT NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
            source_entity_id BIGINT NOT NULL REFERENCES knowledge_entities(id) ON DELETE CASCADE,
            target_entity_id BIGINT NOT NULL REFERENCES knowledge_entities(id) ON DELETE CASCADE,
            predicate TEXT NOT NULL,
            document_id BIGINT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_id BIGINT NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
            confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_id, chunk_id, source_entity_id, target_entity_id, predicate)
        )
        """
    )
    op.execute("CREATE INDEX ix_knowledge_entities_lookup ON knowledge_entities (space_id, normalized_name)")
    op.execute("CREATE INDEX ix_knowledge_relations_source ON knowledge_relations (source_entity_id)")
    op.execute("CREATE INDEX ix_knowledge_relations_target ON knowledge_relations (target_entity_id)")
    op.execute("CREATE INDEX ix_knowledge_relations_document ON knowledge_relations (document_id, chunk_id)")


def downgrade() -> None:
    """Drop knowledge graph relations and entities."""
    op.execute("DROP INDEX ix_knowledge_relations_document")
    op.execute("DROP INDEX ix_knowledge_relations_target")
    op.execute("DROP INDEX ix_knowledge_relations_source")
    op.execute("DROP INDEX ix_knowledge_entities_lookup")
    op.execute("DROP TABLE knowledge_relations")
    op.execute("DROP TABLE knowledge_entities")
