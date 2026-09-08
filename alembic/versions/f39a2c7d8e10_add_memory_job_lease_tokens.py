"""Add ownership tokens to memory job leases.

Revision ID: f39a2c7d8e10
Revises: e57d1a3b6c98
Create Date: 2026-09-08 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f39a2c7d8e10"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "e57d1a3b6c98"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the token checked by renew and settlement operations."""
    op.add_column("memory_job", sa.Column("lease_token", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Remove memory job lease ownership tokens."""
    op.drop_column("memory_job", "lease_token")
