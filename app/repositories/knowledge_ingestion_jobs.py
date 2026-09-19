"""PostgreSQL repository for lease-owned personal document ingestion."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import literal_column, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.models.knowledge_ingestion_job import KnowledgeIngestionJob


@dataclass(frozen=True)
class ClaimedKnowledgeIngestionJob:
    """Immutable payload returned only to the worker holding its lease."""

    id: int
    user_id: int
    space_id: int
    space_slug: str
    external_id: str
    original_name: str
    stored_path: str
    content_type: str
    attempts: int
    lease_token: str


class KnowledgeIngestionJobRepository:
    """Enqueue, claim and settle ingestion jobs with token-aware ownership."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Store the shared async session factory."""
        self._session_factory = session_factory

    async def enqueue(self, **values: Any) -> tuple[int, bool]:
        """Insert one idempotent job and return its stable identifier."""
        statement = (
            insert(KnowledgeIngestionJob)
            .values(**values, status="pending", attempts=0, available_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=[KnowledgeIngestionJob.idempotency_key])
            .returning(literal_column("id"))
        )
        async with self._session_factory() as session, session.begin():
            result = await session.exec(statement)
            job_id = result.scalar_one_or_none()
            if job_id is not None:
                return int(job_id), True
            existing = await session.exec(
                select(KnowledgeIngestionJob).where(
                    KnowledgeIngestionJob.idempotency_key == values["idempotency_key"]
                )
            )
            row = existing.one()
            if row.id is None:
                raise RuntimeError("existing ingestion job has no identifier")
            return int(row.id), False

    async def get_for_user(self, job_id: int, user_id: int) -> KnowledgeIngestionJob | None:
        """Read a job only through its authenticated owner."""
        async with self._session_factory() as session:
            result = await session.exec(
                select(KnowledgeIngestionJob).where(
                    KnowledgeIngestionJob.id == job_id,
                    KnowledgeIngestionJob.user_id == user_id,
                )
            )
            return result.one_or_none()

    async def claim(self, stale_after_seconds: int) -> ClaimedKnowledgeIngestionJob | None:
        """Atomically claim one due or stale job with SKIP LOCKED."""
        lease_token = uuid4().hex
        statement: Any = text(
            """
            WITH candidate AS (
                SELECT id FROM knowledge_ingestion_jobs
                WHERE (status = 'pending' AND available_at <= now())
                   OR (status = 'processing' AND locked_at < now() - make_interval(secs => :stale_after_seconds))
                ORDER BY available_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE knowledge_ingestion_jobs AS job
            SET status = 'processing', locked_at = now(), lease_token = :lease_token,
                attempts = job.attempts + 1
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING job.id, job.user_id, job.space_id, job.space_slug, job.external_id,
                      job.original_name, job.stored_path, job.content_type, job.attempts, job.lease_token
            """
        )
        async with self._session_factory() as session, session.begin():
            result = await session.exec(
                statement,
                params={"stale_after_seconds": stale_after_seconds, "lease_token": lease_token},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return ClaimedKnowledgeIngestionJob(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            space_id=int(row["space_id"]),
            space_slug=str(row["space_slug"]),
            external_id=str(row["external_id"]),
            original_name=str(row["original_name"]),
            stored_path=str(row["stored_path"]),
            content_type=str(row["content_type"]),
            attempts=int(row["attempts"]),
            lease_token=str(row["lease_token"]),
        )

    async def renew(self, job_id: int, lease_token: str) -> bool:
        """Renew only the caller's active lease."""
        statement: Any = text(
            """
            UPDATE knowledge_ingestion_jobs SET locked_at = now()
            WHERE id = :job_id AND status = 'processing' AND lease_token = :lease_token
            RETURNING id
            """
        )
        async with self._session_factory() as session, session.begin():
            result = await session.exec(statement, params={"job_id": job_id, "lease_token": lease_token})
            return result.scalar_one_or_none() is not None

    async def succeed(self, job_id: int, lease_token: str, document_id: int) -> bool:
        """Complete only a job still owned by the current worker."""
        statement: Any = text(
            """
            UPDATE knowledge_ingestion_jobs
            SET status = 'completed', document_id = :document_id, locked_at = NULL,
                lease_token = NULL, last_error = NULL
            WHERE id = :job_id AND status = 'processing' AND lease_token = :lease_token
            RETURNING id
            """
        )
        async with self._session_factory() as session, session.begin():
            result = await session.exec(
                statement,
                params={"job_id": job_id, "lease_token": lease_token, "document_id": document_id},
            )
            return result.scalar_one_or_none() is not None

    async def fail(self, job_id: int, lease_token: str, attempts: int, max_attempts: int, error: str) -> bool:
        """Retry with exponential delay or retain a terminal failure."""
        terminal = attempts >= max_attempts
        statement: Any = text(
            """
            UPDATE knowledge_ingestion_jobs
            SET status = :status, available_at = now() + make_interval(secs => :delay_seconds),
                locked_at = NULL, lease_token = NULL, last_error = :error
            WHERE id = :job_id AND status = 'processing' AND lease_token = :lease_token
            RETURNING id
            """
        )
        async with self._session_factory() as session, session.begin():
            result = await session.exec(
                statement,
                params={
                    "status": "failed" if terminal else "pending",
                    "delay_seconds": min(300, 2 ** max(0, attempts - 1)),
                    "error": error[:2000],
                    "job_id": job_id,
                    "lease_token": lease_token,
                },
            )
            return result.scalar_one_or_none() is not None
