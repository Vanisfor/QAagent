"""Durable outbox worker for long-term-memory persistence."""

import asyncio
import hashlib
import json
from typing import Any

from app.core.config import settings
from app.core.logging import logger
from app.repositories.memory_jobs import ClaimedMemoryJob, MemoryJobRepository
from app.services.database import database_service
from app.services.memory import memory_service


class MemoryJobLeaseLost(RuntimeError):
    """Raised when another worker has taken ownership of a stale job."""


class MemoryJobService:
    """Persist memory writes before processing them in a recoverable worker."""

    def __init__(self) -> None:
        """Initialize the worker without starting background work."""
        self._repository = MemoryJobRepository(database_service.session_factory)
        self._stop = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    async def enqueue(
        self,
        user_id: str | None,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Durably enqueue one idempotent memory update."""
        if user_id is None:
            return False
        safe_metadata = metadata or {}
        canonical = json.dumps(
            {"user_id": str(user_id), "messages": messages, "metadata": safe_metadata},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        idempotency_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        inserted = await self._repository.enqueue(
            idempotency_key=idempotency_key,
            user_id=str(user_id),
            messages=messages,
            metadata=safe_metadata,
        )
        logger.info("memory_job_enqueued", inserted=inserted, user_id=user_id)
        return inserted

    async def start(self) -> None:
        """Start the single-process polling worker."""
        if self._worker is not None and not self._worker.done():
            return
        self._stop.clear()
        self._worker = asyncio.create_task(self._run(), name="memory-job-worker")
        logger.info("memory_job_worker_started")

    async def stop(self) -> None:
        """Stop the worker after its current job or a bounded grace period."""
        self._stop.set()
        if self._worker is None:
            return
        try:
            await asyncio.wait_for(self._worker, timeout=settings.MEMORY_JOB_SHUTDOWN_TIMEOUT)
        except TimeoutError:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        finally:
            self._worker = None
        logger.info("memory_job_worker_stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = await self._repository.claim(settings.MEMORY_JOB_STALE_AFTER_SECONDS)
                if job is None:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=settings.MEMORY_JOB_POLL_SECONDS)
                    except TimeoutError:
                        pass
                    continue

                try:
                    await self._process_with_heartbeat(job)
                except MemoryJobLeaseLost:
                    logger.warning("memory_job_lease_lost", job_id=job.id, attempt=job.attempts)
                except Exception as error:
                    settled = await self._repository.fail(
                        job.id,
                        job.lease_token,
                        job.attempts,
                        settings.MEMORY_JOB_MAX_ATTEMPTS,
                        f"{type(error).__name__}: {error}",
                    )
                    logger.exception(
                        "memory_job_processing_failed",
                        job_id=job.id,
                        attempt=job.attempts,
                        terminal=job.attempts >= settings.MEMORY_JOB_MAX_ATTEMPTS,
                        lease_owned=settled,
                    )
                else:
                    settled = await self._repository.succeed(job.id, job.lease_token)
                    if settled:
                        logger.info("memory_job_processed", job_id=job.id, attempt=job.attempts)
                    else:
                        logger.warning("memory_job_settlement_rejected", job_id=job.id, attempt=job.attempts)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("memory_job_worker_iteration_failed", error=str(error))
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=settings.MEMORY_JOB_POLL_SECONDS)
                except TimeoutError:
                    pass

    async def _process_with_heartbeat(self, job: ClaimedMemoryJob) -> None:
        """Process one job while periodically renewing its ownership lease."""
        heartbeat_seconds = max(
            0.1,
            min(settings.MEMORY_JOB_HEARTBEAT_SECONDS, settings.MEMORY_JOB_STALE_AFTER_SECONDS / 3),
        )
        processing = asyncio.create_task(memory_service.add(job.user_id, job.messages, job.metadata))
        try:
            while not processing.done():
                done, _ = await asyncio.wait({processing}, timeout=heartbeat_seconds)
                if processing in done:
                    break
                if not await self._repository.renew(job.id, job.lease_token):
                    raise MemoryJobLeaseLost(f"memory job {job.id} lease ownership was lost")
            await processing
        finally:
            if not processing.done():
                processing.cancel()
                await asyncio.gather(processing, return_exceptions=True)


memory_job_service = MemoryJobService()
