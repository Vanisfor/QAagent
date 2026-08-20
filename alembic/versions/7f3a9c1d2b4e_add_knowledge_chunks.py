"""Add knowledge_chunks table for the RAG knowledge base.

Revision ID: 7f3a9c1d2b4e
Revises: b25d38b0cd7c
Create Date: 2026-08-15 20:00:00.000000

The DDL is intentionally closed (hardcoded table name and vector
dimension) so the migration outcome does not depend on runtime
environment variables. ``KNOWLEDGE_TABLE`` / ``EMBEDDING_DIM`` settings
must match these defaults.

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f3a9c1d2b4e"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b25d38b0cd7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Closed values — keep in sync with app/core/config.py defaults.
_KNOWLEDGE_TABLE = "knowledge_chunks"
_EMBEDDING_DIM = 1024


def upgrade() -> None:
    """Create the knowledge_chunks table with a pgvector column and HNSW index."""
    # Ensure the pgvector extension exists (safe to run if already enabled by mem0;
    # requires superuser on managed PostgreSQL — see docs/rag.md).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        f"""
        CREATE TABLE {_KNOWLEDGE_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            content TEXT NOT NULL,
            source TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            embedding vector({_EMBEDDING_DIM}) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Unique (source, content_hash) makes ingestion idempotent.
    op.execute(
        f"""
        CREATE UNIQUE INDEX ix_knowledge_chunks_source_hash
        ON {_KNOWLEDGE_TABLE} (source, content_hash)
        """
    )

    op.execute(
        f"""
        CREATE INDEX ix_knowledge_chunks_embedding
        ON {_KNOWLEDGE_TABLE} USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
    op.create_index("ix_knowledge_chunks_source", _KNOWLEDGE_TABLE, ["source"])


def downgrade() -> None:
    """Drop the knowledge_chunks table."""
    op.drop_index("ix_knowledge_chunks_source", table_name=_KNOWLEDGE_TABLE)
    op.execute(f"DROP TABLE IF EXISTS {_KNOWLEDGE_TABLE}")
