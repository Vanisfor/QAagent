"""LangGraph PostgreSQL checkpoint pool lifecycle."""

from typing import TypeAlias
from urllib.parse import quote_plus

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Environment, settings
from app.core.logging import logger

PostgresConnPool: TypeAlias = AsyncConnectionPool[AsyncConnection[DictRow]]


class CheckpointService:
    """Own the dedicated psycopg pool required by AsyncPostgresSaver."""

    def __init__(self) -> None:
        """Initialize the lazily opened pool."""
        self._pool: PostgresConnPool | None = None

    @property
    def is_ready(self) -> bool:
        """Return whether the checkpoint pool is open."""
        return self._pool is not None and not self._pool.closed

    async def get_pool(self) -> PostgresConnPool | None:
        """Open and return the checkpoint pool."""
        if self._pool is not None:
            return self._pool
        try:
            connection_url = (
                "postgresql://"
                f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
            )
            pool = AsyncConnectionPool[AsyncConnection[DictRow]](
                connection_url,
                open=False,
                max_size=settings.POSTGRES_POOL_SIZE,
                kwargs={
                    "autocommit": True,
                    "connect_timeout": 5,
                    "prepare_threshold": None,
                    "row_factory": dict_row,
                },
            )
            await pool.open()
            self._pool = pool
            logger.info(
                "checkpoint_connection_pool_created",
                max_size=settings.POSTGRES_POOL_SIZE,
                environment=settings.ENVIRONMENT.value,
            )
        except Exception as error:
            logger.exception(
                "checkpoint_connection_pool_creation_failed",
                error=str(error),
                environment=settings.ENVIRONMENT.value,
            )
            if settings.ENVIRONMENT != Environment.PRODUCTION:
                raise
            return None
        return self._pool

    async def create_saver(self) -> AsyncPostgresSaver | None:
        """Create and initialize the LangGraph saver."""
        pool = await self.get_pool()
        if pool is None:
            return None
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        return saver

    async def close(self) -> None:
        """Close the checkpoint pool."""
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None
        logger.info("checkpoint_connection_pool_closed")
