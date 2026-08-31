"""Add durable enterprise connector synchronization state.

Revision ID: f27c6e9a4b31
Revises: e41a8c7d2f90
Create Date: 2026-08-31 15:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f27c6e9a4b31"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "e41a8c7d2f90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create connector/run ledgers and associate documents with connectors."""
    op.execute(
        """
        CREATE TABLE knowledge_connectors (
            id BIGSERIAL PRIMARY KEY,
            space_id BIGINT NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            sync_cursor JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'idle'
                CHECK (status IN ('idle', 'running', 'failed', 'disabled')),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (space_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_sync_runs (
            id BIGSERIAL PRIMARY KEY,
            connector_id BIGINT NOT NULL REFERENCES knowledge_connectors(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            cursor_before JSONB NOT NULL DEFAULT '{}'::jsonb,
            cursor_after JSONB NOT NULL DEFAULT '{}'::jsonb,
            documents_seen INTEGER NOT NULL DEFAULT 0,
            documents_upserted INTEGER NOT NULL DEFAULT 0,
            documents_deleted INTEGER NOT NULL DEFAULT 0,
            chunks_upserted INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ
        )
        """
    )
    op.execute("ALTER TABLE knowledge_documents ADD COLUMN connector_id BIGINT")
    op.execute(
        "ALTER TABLE knowledge_documents ADD CONSTRAINT fk_knowledge_documents_connector "
        "FOREIGN KEY (connector_id) REFERENCES knowledge_connectors(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX ix_knowledge_connectors_space ON knowledge_connectors (space_id)")
    op.execute("CREATE INDEX ix_knowledge_sync_runs_connector ON knowledge_sync_runs (connector_id, started_at DESC)")
    op.execute("CREATE INDEX ix_knowledge_documents_connector ON knowledge_documents (connector_id)")


def downgrade() -> None:
    """Remove connector synchronization state."""
    op.execute("DROP INDEX ix_knowledge_documents_connector")
    op.execute("DROP INDEX ix_knowledge_sync_runs_connector")
    op.execute("DROP INDEX ix_knowledge_connectors_space")
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT fk_knowledge_documents_connector")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN connector_id")
    op.execute("DROP TABLE knowledge_sync_runs")
    op.execute("DROP TABLE knowledge_connectors")
