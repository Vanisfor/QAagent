"""Add durable outbox for long-term-memory writes.

Revision ID: d92b87f10e42
Revises: c84d51a9207e
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d92b87f10e42"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c84d51a9207e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the durable memory job table."""
    op.create_table(
        "memory_job",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_memory_job_status_available", "memory_job", ["status", "available_at"])
    op.create_index("ix_memory_job_user_id", "memory_job", ["user_id"])


def downgrade() -> None:
    """Drop the durable memory job table."""
    op.drop_index("ix_memory_job_user_id", table_name="memory_job")
    op.drop_index("ix_memory_job_status_available", table_name="memory_job")
    op.drop_table("memory_job")
