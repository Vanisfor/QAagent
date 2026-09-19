"""Add profile, preferences and revocable login sessions without replacing identities."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a61d09e47b32"
down_revision = "f39a2c7d8e10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Retain existing users, foreign keys, credentials and conversations."""
    op.add_column("user", sa.Column("status", sa.String(16), nullable=False, server_default="active"))
    op.add_column("user", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("user", sa.Column("last_login_at", sa.DateTime(timezone=True)))
    op.create_table("user_profiles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("display_name", sa.String(50), nullable=False),
        sa.Column("avatar_file", sa.String(64)),
        sa.Column("bio", sa.String(500), nullable=False, server_default=""),
        sa.Column("language", sa.String(8), nullable=False, server_default="auto"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"))
    op.execute('INSERT INTO user_profiles (user_id, display_name) SELECT id, LEFT(COALESCE(NULLIF(username, \'\'), split_part(email, \'@\', 1)), 50) FROM "user"')
    for table, field in (("user_settings", "appearance"), ("user_personalization", "preferences")):
        op.create_table(table,
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
            sa.Column(field, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        op.execute(f'INSERT INTO {table} (user_id) SELECT id FROM "user"')
    op.create_table("user_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("device", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])


def downgrade() -> None:
    """Remove v1 additions while retaining pre-existing users and chats."""
    for table in ("user_sessions", "user_personalization", "user_settings", "user_profiles"):
        op.drop_table(table)
    for column in ("last_login_at", "updated_at", "status"):
        op.drop_column("user", column)
