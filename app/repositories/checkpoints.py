"""LangGraph checkpoint persistence operations."""

from typing import Any

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.logging import logger


class CheckpointRepository:
    """Delete checkpoint rows inside a caller-owned SQL transaction."""

    _ALLOWED_TABLES = frozenset({"checkpoint_blobs", "checkpoint_writes", "checkpoints"})

    async def delete_thread(self, session: AsyncSession, thread_id: str) -> None:
        """Delete every checkpoint row for one LangGraph thread."""
        tables = tuple(settings.CHECKPOINT_TABLES)
        if not tables or any(table not in self._ALLOWED_TABLES for table in tables):
            raise RuntimeError("invalid checkpoint table configuration")

        for table in tables:
            statement: Any = text(f'DELETE FROM "{table}" WHERE thread_id = :thread_id')
            await session.exec(statement, params={"thread_id": thread_id})

        logger.info("checkpoint_tables_cleared_for_session", tables=tables, session_id=thread_id)
