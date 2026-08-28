"""PostgreSQL repository for durable long-term-memory jobs."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import literal_column, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.memory_job import MemoryJob


@dataclass(frozen=True)
class ClaimedMemoryJob:
    """A claimed job payload returned to the worker."""

    id: int
    user_id: str
    messages: list[dict[str, Any]]
    metadata: dict[str, Any]
    attempts: int


class MemoryJobRepository:
    """Enqueue, claim, and settle memory jobs via the shared pool."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Store the shared async session factory."""
        self._session_factory = session_factory

    async def enqueue(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> bool:
        """Insert a pending job unless its idempotency key already exists."""
        statement = (
            insert(MemoryJob)
            .values(
                idempotency_key=idempotency_key,
                user_id=user_id,
                messages=messages,
                job_metadata=metadata,
                status="pending",
                attempts=0,
                available_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=[MemoryJob.idempotency_key])
            .returning(literal_column("id"))
        )
        async with self._session_factory() as session:
            result = await session.exec(statement)
            await session.commit()
            return result.scalar_one_or_none() is not None

    async def claim(self, stale_after_seconds: int) -> ClaimedMemoryJob | None:
        """Atomically claim one due or stale job using SKIP LOCKED."""
        statement: Any = text(
            """
            WITH candidate AS (
                SELECT id
                FROM memory_job
                WHERE (
                    status = 'pending' AND available_at <= now()
                ) OR (
                    status = 'processing'
                    AND locked_at < now() - make_interval(secs => :stale_after_seconds)
                )
                ORDER BY available_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE memory_job AS job
            SET status = 'processing', locked_at = now(), attempts = job.attempts + 1
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING job.id, job.user_id, job.messages, job.metadata, job.attempts
            """
        )
        async with self._session_factory() as session, session.begin():
            result = await session.exec(
                statement,
                params={"stale_after_seconds": stale_after_seconds},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return ClaimedMemoryJob(
            id=int(row["id"]),
            user_id=str(row["user_id"]),
            messages=list(row["messages"]),
            metadata=dict(row["metadata"]),
            attempts=int(row["attempts"]),
        )

    async def succeed(self, job_id: int) -> None:
        """Complete a job, retaining only its idempotency ledger entry."""
        async with self._session_factory() as session, session.begin():
            statement: Any = text(
                """
                UPDATE memory_job
                SET status = 'completed',
                    messages = '[]'::jsonb,
                    metadata = '{}'::jsonb,
                    locked_at = NULL,
                    last_error = NULL
                WHERE id = :job_id
                """
            )
            await session.exec(
                statement,
                params={"job_id": job_id},
            )

    async def fail(self, job_id: int, attempts: int, max_attempts: int, error: str) -> None:
        """Retry with exponential delay or park a permanently failed job."""
        terminal = attempts >= max_attempts
        delay_seconds = min(300, 2 ** max(0, attempts - 1))
        statement: Any = text(
            """
            UPDATE memory_job
            SET status = :status,
                available_at = now() + make_interval(secs => :delay_seconds),
                locked_at = NULL,
                last_error = :error
            WHERE id = :job_id
            """
        )
        params = {
            "status": "failed" if terminal else "pending",
            "delay_seconds": delay_seconds,
            "error": error[:2000],
            "job_id": job_id,
        }
        async with self._session_factory() as session, session.begin():
            await session.exec(
                statement,
                params=params,
            )
