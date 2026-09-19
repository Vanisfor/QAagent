"""Add durable owner-scoped personal knowledge ingestion jobs."""

import sqlalchemy as sa

from alembic import op

revision = "b72e14c8a901"
down_revision = "a61d09e47b32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the lease-owned ingestion queue and owner lookup indexes."""
    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "space_id", sa.BigInteger(), sa.ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("space_slug", sa.String(128), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("stored_path", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(32)),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_knowledge_ingestion_status",
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_status_available",
        "knowledge_ingestion_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_knowledge_ingestion_user_created",
        "knowledge_ingestion_jobs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_knowledge_ingestion_space_external",
        "knowledge_ingestion_jobs",
        ["space_id", "external_id"],
        unique=True,
    )


def downgrade() -> None:
    """Remove only the personal ingestion queue."""
    op.drop_table("knowledge_ingestion_jobs")
