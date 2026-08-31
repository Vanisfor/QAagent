"""Add organization boundaries, external groups and connector credentials.

Revision ID: c35b7e1f9a64
Revises: a13f8d4c7e52
Create Date: 2026-08-31 20:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c35b7e1f9a64"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "a13f8d4c7e52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create tenant and external identity boundaries and backfill legacy data."""
    op.execute(
        """
        CREATE TABLE organizations (
            id BIGSERIAL PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE organization_members (
            organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin', 'owner')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (organization_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_groups (
            id BIGSERIAL PRIMARY KEY,
            organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            connector_id BIGINT REFERENCES knowledge_connectors(id) ON DELETE CASCADE,
            external_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (organization_id, connector_id, external_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_group_members (
            group_id BIGINT NOT NULL REFERENCES knowledge_groups(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (group_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE connector_external_principals (
            connector_id BIGINT NOT NULL REFERENCES knowledge_connectors(id) ON DELETE CASCADE,
            principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'group')),
            external_id TEXT NOT NULL,
            display_name TEXT,
            mapped_user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
            mapped_group_id BIGINT REFERENCES knowledge_groups(id) ON DELETE SET NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (connector_id, principal_type, external_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_connector_credentials (
            connector_id BIGINT PRIMARY KEY REFERENCES knowledge_connectors(id) ON DELETE CASCADE,
            encrypted_secret TEXT NOT NULL,
            key_version INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute("INSERT INTO organizations (id, slug, name) VALUES (1, 'default', 'Default organization')")
    op.execute("SELECT setval(pg_get_serial_sequence('organizations', 'id'), 1, true)")
    op.execute(
        """
        INSERT INTO organization_members (organization_id, user_id, role)
        SELECT 1, id, 'member' FROM "user"
        ON CONFLICT DO NOTHING
        """
    )
    op.execute("ALTER TABLE knowledge_spaces ADD COLUMN organization_id BIGINT")
    op.execute("UPDATE knowledge_spaces SET organization_id = 1")
    op.execute("ALTER TABLE knowledge_spaces ALTER COLUMN organization_id SET NOT NULL")
    op.execute(
        "ALTER TABLE knowledge_spaces ADD CONSTRAINT fk_knowledge_spaces_organization "
        "FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE"
    )
    op.execute("CREATE INDEX ix_knowledge_spaces_organization ON knowledge_spaces (organization_id)")
    op.execute("CREATE INDEX ix_organization_members_user ON organization_members (user_id, organization_id)")
    op.execute("CREATE INDEX ix_knowledge_group_members_user ON knowledge_group_members (user_id, group_id)")
    op.execute(
        "CREATE INDEX ix_connector_external_principals_mapping ON connector_external_principals (mapped_user_id, mapped_group_id)"
    )


def downgrade() -> None:
    """Remove tenant and external identity boundaries."""
    op.execute("DROP INDEX ix_connector_external_principals_mapping")
    op.execute("DROP INDEX ix_knowledge_group_members_user")
    op.execute("DROP INDEX ix_organization_members_user")
    op.execute("DROP INDEX ix_knowledge_spaces_organization")
    op.execute("ALTER TABLE knowledge_spaces DROP CONSTRAINT fk_knowledge_spaces_organization")
    op.execute("ALTER TABLE knowledge_spaces DROP COLUMN organization_id")
    op.execute("DROP TABLE knowledge_connector_credentials")
    op.execute("DROP TABLE connector_external_principals")
    op.execute("DROP TABLE knowledge_group_members")
    op.execute("DROP TABLE knowledge_groups")
    op.execute("DROP TABLE organization_members")
    op.execute("DROP TABLE organizations")
