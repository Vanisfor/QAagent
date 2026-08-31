"""Shared-pool repository for durable knowledge connector synchronization."""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession


@dataclass(frozen=True)
class KnowledgeConnectorRecord:
    """Connector configuration and durable cursor loaded for one sync run."""

    id: int
    space_slug: str
    kind: str
    name: str
    config: dict[str, Any]
    cursor: dict[str, Any]
    status: str


class KnowledgeSyncRepository:
    """Persist connectors and content-free synchronization ledgers."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Store the application's shared async session factory."""
        self._session_factory = session_factory

    async def create_local_connector(self, space_slug: str, name: str, root_path: str) -> int:
        """Register or update a local connector without storing credentials."""
        statement: Any = text(
            """
            INSERT INTO knowledge_connectors (space_id, kind, name, config)
            SELECT space.id, 'local', :name, jsonb_build_object('root_path', CAST(:root_path AS text))
            FROM knowledge_spaces AS space
            WHERE space.slug = :space_slug
            ON CONFLICT (space_id, name) DO UPDATE
            SET config = EXCLUDED.config,
                updated_at = now()
            RETURNING id
            """
        )
        async with self._session_factory() as session, session.begin():
            result = await session.exec(
                statement,
                params={"space_slug": space_slug, "name": name, "root_path": root_path},
            )
            connector_id = result.scalar_one_or_none()
        if connector_id is None:
            raise ValueError(f"knowledge space not found: {space_slug}")
        return int(connector_id)

    async def get_connector(self, connector_id: int) -> KnowledgeConnectorRecord:
        """Load one connector and its owning space."""
        statement: Any = text(
            """
            SELECT connector.id, space.slug AS space_slug, connector.kind,
                   connector.name, connector.config, connector.sync_cursor,
                   connector.status
            FROM knowledge_connectors AS connector
            JOIN knowledge_spaces AS space ON space.id = connector.space_id
            WHERE connector.id = :connector_id
            """
        )
        async with self._session_factory() as session:
            result = await session.exec(statement, params={"connector_id": connector_id})
            row = result.mappings().first()
        if row is None:
            raise ValueError(f"knowledge connector not found: {connector_id}")
        return KnowledgeConnectorRecord(
            id=int(row["id"]),
            space_slug=str(row["space_slug"]),
            kind=str(row["kind"]),
            name=str(row["name"]),
            config=dict(row["config"] or {}),
            cursor=dict(row["sync_cursor"] or {}),
            status=str(row["status"]),
        )

    async def start_run(self, connector: KnowledgeConnectorRecord) -> int:
        """Create a running ledger and mark the connector busy atomically."""
        update_statement: Any = text(
            """
            UPDATE knowledge_connectors
            SET status = 'running', updated_at = now()
            WHERE id = :connector_id AND status IN ('idle', 'failed')
            RETURNING id
            """
        )
        insert_statement: Any = text(
            """
            INSERT INTO knowledge_sync_runs (connector_id, status, cursor_before)
            VALUES (:connector_id, 'running', CAST(:cursor AS jsonb))
            RETURNING id
            """
        )
        async with self._session_factory() as session, session.begin():
            updated = await session.exec(update_statement, params={"connector_id": connector.id})
            if updated.scalar_one_or_none() is None:
                raise RuntimeError("knowledge connector is disabled or already running")
            result = await session.exec(
                insert_statement,
                params={"connector_id": connector.id, "cursor": json.dumps(connector.cursor)},
            )
            run_id = result.scalar_one()
        return int(run_id)

    async def complete_run(
        self,
        connector_id: int,
        run_id: int,
        next_cursor: dict[str, Any],
        *,
        documents_seen: int,
        documents_upserted: int,
        documents_deleted: int,
        chunks_upserted: int,
    ) -> None:
        """Advance the cursor only after every document and tombstone succeeds."""
        params = {
            "connector_id": connector_id,
            "run_id": run_id,
            "cursor": json.dumps(next_cursor),
            "documents_seen": documents_seen,
            "documents_upserted": documents_upserted,
            "documents_deleted": documents_deleted,
            "chunks_upserted": chunks_upserted,
        }
        update_connector: Any = text(
            """
            UPDATE knowledge_connectors
            SET sync_cursor = CAST(:cursor AS jsonb), status = 'idle',
                last_synced_at = now(), updated_at = now()
            WHERE id = :connector_id
            """
        )
        update_run: Any = text(
            """
            UPDATE knowledge_sync_runs
            SET status = 'completed', cursor_after = CAST(:cursor AS jsonb),
                documents_seen = :documents_seen,
                documents_upserted = :documents_upserted,
                documents_deleted = :documents_deleted,
                chunks_upserted = :chunks_upserted,
                finished_at = now()
            WHERE id = :run_id AND connector_id = :connector_id
            """
        )
        async with self._session_factory() as session, session.begin():
            await session.exec(update_connector, params=params)
            await session.exec(update_run, params=params)

    async def fail_run(self, connector_id: int, run_id: int, error_code: str) -> None:
        """Mark a failed run without advancing or exposing source content."""
        params = {"connector_id": connector_id, "run_id": run_id, "error_code": error_code[:120]}
        update_connector: Any = text(
            "UPDATE knowledge_connectors SET status = 'failed', updated_at = now() WHERE id = :connector_id"
        )
        update_run: Any = text(
            """
            UPDATE knowledge_sync_runs
            SET status = 'failed', error_code = :error_code, finished_at = now()
            WHERE id = :run_id AND connector_id = :connector_id
            """
        )
        async with self._session_factory() as session, session.begin():
            await session.exec(update_connector, params=params)
            await session.exec(update_run, params=params)
