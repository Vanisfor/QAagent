"""PostgreSQL integration coverage for session/checkpoint lifecycle."""

import asyncio
import os
import selectors
from typing import Any, cast
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from sqlalchemy import text

from app.core.config import settings
from app.repositories.memory_jobs import MemoryJobRepository
from app.services.database import database_service

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_session_delete_atomically_clears_checkpoint() -> None:
    """Deleting a session removes the corresponding LangGraph checkpoint rows."""

    async def run() -> None:
        session_id = str(uuid4())
        email = f"integration-{uuid4()}@example.com"
        connection_url = (
            f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
            f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
        )
        connection = await AsyncConnection.connect(connection_url, autocommit=True)
        try:
            await AsyncPostgresSaver(connection).setup()
            user = await database_service.create_user(email, "unused")
            await database_service.create_session(session_id, user.id)
            await connection.execute(
                """
                INSERT INTO checkpoints (
                    thread_id, checkpoint_ns, checkpoint_id,
                    parent_checkpoint_id, type, checkpoint, metadata
                ) VALUES (%s, '', %s, NULL, 'json', '{}'::jsonb, '{}'::jsonb)
                """,
                (session_id, str(uuid4())),
            )

            assert await database_service.delete_session(session_id) is True
            remaining = await connection.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id = %s",
                (session_id,),
            )
            assert (await remaining.fetchone())[0] == 0
            assert await database_service.get_session(session_id) is None
        finally:
            await database_service.delete_user_by_email(email)
            await database_service.engine.dispose()
            await connection.close()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1")
def test_memory_job_claim_and_completion_are_durable_and_idempotent() -> None:
    """The outbox survives queue boundaries and retains a content-free dedupe ledger."""

    async def run() -> None:
        repository = MemoryJobRepository(database_service.session_factory)
        key = uuid4().hex
        try:
            assert await repository.enqueue(
                idempotency_key=key,
                user_id="7",
                messages=[{"role": "user", "content": "remember this"}],
                metadata={"session_id": "integration"},
            )
            assert not await repository.enqueue(
                idempotency_key=key,
                user_id="7",
                messages=[{"role": "user", "content": "remember this"}],
                metadata={"session_id": "integration"},
            )

            job = await repository.claim(stale_after_seconds=300)
            assert job is not None
            assert job.messages[0]["content"] == "remember this"
            await repository.succeed(job.id)

            async with database_service.session_factory() as session:
                result = await session.exec(
                    cast(Any, text("SELECT status, messages, metadata FROM memory_job WHERE id = :job_id")),
                    params={"job_id": job.id},
                )
                row = result.mappings().one()
            assert row["status"] == "completed"
            assert row["messages"] == []
            assert row["metadata"] == {}
            assert not await repository.enqueue(
                idempotency_key=key,
                user_id="7",
                messages=[],
                metadata={},
            )
        finally:
            async with database_service.session_factory() as session, session.begin():
                await session.exec(
                    cast(Any, text("DELETE FROM memory_job WHERE idempotency_key = :key")),
                    params={"key": key},
                )
            await database_service.engine.dispose()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
