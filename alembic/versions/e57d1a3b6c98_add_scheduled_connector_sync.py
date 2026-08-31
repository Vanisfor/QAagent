"""Add scheduled connector synchronization fields.

Revision ID: e57d1a3b6c98
Revises: d46c9f2a8b75
Create Date: 2026-08-31 22:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e57d1a3b6c98"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "d46c9f2a8b75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add durable due-time scheduling for background connector runs."""
    op.execute("ALTER TABLE knowledge_connectors ADD COLUMN sync_interval_seconds INTEGER NOT NULL DEFAULT 900")
    op.execute("ALTER TABLE knowledge_connectors ADD COLUMN next_sync_at TIMESTAMPTZ NOT NULL DEFAULT now()")
    op.execute(
        "ALTER TABLE knowledge_connectors ADD CONSTRAINT ck_connector_sync_interval CHECK (sync_interval_seconds >= 60)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_connectors_due "
        "ON knowledge_connectors (next_sync_at, id) WHERE status IN ('idle', 'failed')"
    )


def downgrade() -> None:
    """Remove scheduled synchronization fields."""
    op.execute("DROP INDEX ix_knowledge_connectors_due")
    op.execute("ALTER TABLE knowledge_connectors DROP CONSTRAINT ck_connector_sync_interval")
    op.execute("ALTER TABLE knowledge_connectors DROP COLUMN next_sync_at")
    op.execute("ALTER TABLE knowledge_connectors DROP COLUMN sync_interval_seconds")
