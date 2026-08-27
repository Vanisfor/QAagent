"""Add encrypted per-user LLM settings.

Revision ID: c84d51a9207e
Revises: 7f3a9c1d2b4e
Create Date: 2026-08-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c84d51a9207e"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "7f3a9c1d2b4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the per-user LLM settings table."""
    op.create_table(
        "user_llm_settings",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("api_key_last_four", sa.String(length=4), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("thinking_enabled", sa.Boolean(), nullable=False),
        sa.Column("validated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_llm_settings_user_id", "user_llm_settings", ["user_id"], unique=True)


def downgrade() -> None:
    """Drop the per-user LLM settings table."""
    op.drop_index("ix_user_llm_settings_user_id", table_name="user_llm_settings")
    op.drop_table("user_llm_settings")
