"""Add knowledge spaces, documents, ACLs and lexical indexing.

Revision ID: e41a8c7d2f90
Revises: d92b87f10e42
Create Date: 2026-08-31 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e41a8c7d2f90"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "d92b87f10e42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Normalize the global chunk table and add additive read ACLs."""
    op.execute(
        """
        CREATE TABLE knowledge_spaces (
            id BIGSERIAL PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            is_public BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_documents (
            id BIGSERIAL PRIMARY KEY,
            space_id BIGINT NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
            source_type TEXT NOT NULL DEFAULT 'local',
            external_id TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_updated_at TIMESTAMPTZ,
            deleted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (space_id, source_type, external_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_space_principals (
            space_id BIGINT NOT NULL REFERENCES knowledge_spaces(id) ON DELETE CASCADE,
            principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'group')),
            principal_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'reader' CHECK (role IN ('reader', 'editor', 'owner')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (space_id, principal_type, principal_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_document_principals (
            document_id BIGINT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'group')),
            principal_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (document_id, principal_type, principal_id)
        )
        """
    )

    op.execute(
        """
        INSERT INTO knowledge_spaces (id, slug, name, is_public)
        VALUES (1, 'default-public', 'Default public knowledge', true)
        """
    )
    op.execute("SELECT setval(pg_get_serial_sequence('knowledge_spaces', 'id'), 1, true)")

    op.execute(
        """
        INSERT INTO knowledge_documents (
            space_id, source_type, external_id, source, title, content_hash, metadata
        )
        SELECT 1, 'local', source, source, source,
               md5(string_agg(content_hash, '' ORDER BY id)),
               '{}'::jsonb
        FROM knowledge_chunks
        GROUP BY source
        """
    )

    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN space_id BIGINT")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN document_id BIGINT")
    op.execute(
        """
        UPDATE knowledge_chunks AS chunk
        SET space_id = document.space_id,
            document_id = document.id
        FROM knowledge_documents AS document
        WHERE document.space_id = 1
          AND document.source_type = 'local'
          AND document.external_id = chunk.source
        """
    )
    op.execute("ALTER TABLE knowledge_chunks ALTER COLUMN space_id SET NOT NULL")
    op.execute("ALTER TABLE knowledge_chunks ALTER COLUMN document_id SET NOT NULL")
    op.execute(
        "ALTER TABLE knowledge_chunks ADD CONSTRAINT fk_knowledge_chunks_space "
        "FOREIGN KEY (space_id) REFERENCES knowledge_spaces(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE knowledge_chunks ADD CONSTRAINT fk_knowledge_chunks_document "
        "FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE"
    )
    op.execute("DROP INDEX ix_knowledge_chunks_source_hash")
    op.execute("CREATE UNIQUE INDEX ix_knowledge_chunks_document_hash ON knowledge_chunks (document_id, content_hash)")
    op.execute("CREATE INDEX ix_knowledge_chunks_space ON knowledge_chunks (space_id)")
    op.execute("CREATE INDEX ix_knowledge_documents_space ON knowledge_documents (space_id)")
    op.execute(
        "CREATE INDEX ix_knowledge_space_principals_lookup "
        "ON knowledge_space_principals (principal_type, principal_id, space_id)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_document_principals_lookup "
        "ON knowledge_document_principals (principal_type, principal_id, document_id)"
    )
    op.execute(
        "ALTER TABLE knowledge_chunks ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED"
    )
    op.execute("CREATE INDEX ix_knowledge_chunks_search_vector ON knowledge_chunks USING gin (search_vector)")


def downgrade() -> None:
    """Restore the original global chunk schema."""
    op.execute("DROP INDEX ix_knowledge_chunks_search_vector")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN search_vector")
    op.execute("DROP INDEX ix_knowledge_document_principals_lookup")
    op.execute("DROP INDEX ix_knowledge_space_principals_lookup")
    op.execute("DROP INDEX ix_knowledge_documents_space")
    op.execute("DROP INDEX ix_knowledge_chunks_space")
    op.execute("DROP INDEX ix_knowledge_chunks_document_hash")
    op.execute(
        """
        DELETE FROM knowledge_chunks AS duplicate
        USING knowledge_chunks AS keeper
        WHERE duplicate.id > keeper.id
          AND duplicate.source = keeper.source
          AND duplicate.content_hash = keeper.content_hash
        """
    )
    op.execute("CREATE UNIQUE INDEX ix_knowledge_chunks_source_hash ON knowledge_chunks (source, content_hash)")
    op.execute("ALTER TABLE knowledge_chunks DROP CONSTRAINT fk_knowledge_chunks_document")
    op.execute("ALTER TABLE knowledge_chunks DROP CONSTRAINT fk_knowledge_chunks_space")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN document_id")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN space_id")
    op.execute("DROP TABLE knowledge_document_principals")
    op.execute("DROP TABLE knowledge_space_principals")
    op.execute("DROP TABLE knowledge_documents")
    op.execute("DROP TABLE knowledge_spaces")
