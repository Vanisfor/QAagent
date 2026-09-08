"""Cross-worker mutual exclusion for stateful session runs."""

import hashlib
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import logger
from app.services.database import database_service


def _advisory_lock_key(session_id: str) -> int:
    """Map an opaque session ID to PostgreSQL's signed bigint lock key."""
    digest = hashlib.sha256(session_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@dataclass
class SessionRunLease:
    """Own one transaction-scoped PostgreSQL advisory lock."""

    _session: AsyncSession
    _released: bool = False

    async def release(self) -> None:
        """End the transaction, releasing the lock, then return the connection."""
        if self._released:
            return
        self._released = True
        try:
            await self._session.rollback()
        except Exception:
            logger.exception("session_run_lock_release_failed")
        finally:
            await self._session.close()


class SessionRunLockService:
    """Acquire non-blocking session locks that work across API workers."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Store the shared factory; each active run retains one connection."""
        self._session_factory = session_factory

    async def try_acquire(self, session_id: str) -> SessionRunLease | None:
        """Return a lease when no other worker is running this session."""
        session = self._session_factory()
        lock_key = _advisory_lock_key(session_id)
        try:
            result = await session.exec(
                cast(Any, text("SELECT pg_try_advisory_xact_lock(:lock_key)")),
                params={"lock_key": lock_key},
            )
            if not bool(result.scalar_one()):
                await session.close()
                return None
            return SessionRunLease(session)
        except Exception:
            await session.close()
            raise


session_run_lock_service = SessionRunLockService(database_service.session_factory)
