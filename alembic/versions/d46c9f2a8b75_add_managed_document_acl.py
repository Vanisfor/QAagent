"""Track connector-managed document ACL rows.

Revision ID: d46c9f2a8b75
Revises: c35b7e1f9a64
Create Date: 2026-08-31 21:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d46c9f2a8b75"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c35b7e1f9a64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Associate synchronized ACL grants with their owning connector."""
    op.execute("ALTER TABLE knowledge_document_principals ADD COLUMN managed_by_connector_id BIGINT")
    op.execute(
        "ALTER TABLE knowledge_document_principals ADD CONSTRAINT fk_document_principal_connector "
        "FOREIGN KEY (managed_by_connector_id) REFERENCES knowledge_connectors(id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_document_principals_connector "
        "ON knowledge_document_principals (managed_by_connector_id, document_id)"
    )


def downgrade() -> None:
    """Remove connector ownership from document ACL rows."""
    op.execute("DROP INDEX ix_knowledge_document_principals_connector")
    op.execute("ALTER TABLE knowledge_document_principals DROP CONSTRAINT fk_document_principal_connector")
    op.execute("ALTER TABLE knowledge_document_principals DROP COLUMN managed_by_connector_id")
