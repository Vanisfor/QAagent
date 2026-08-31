"""Scheduled background dispatcher for durable connector synchronization."""

import asyncio

from app.core.config import settings
from app.core.logging import logger
from app.repositories.knowledge_sync import KnowledgeSyncRepository
from app.services.database import database_service
from app.services.knowledge_sync import KnowledgeSyncService, knowledge_sync_service


class KnowledgeSyncWorker:
    """Poll due connectors and rely on atomic start_run claims for concurrency safety."""

    def __init__(self, repository: KnowledgeSyncRepository, sync_service: KnowledgeSyncService) -> None:
        """Store shared persistence and synchronization services."""
        self._repository = repository
        self._sync_service = sync_service
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        """Start one background polling task when enabled."""
        if not settings.KNOWLEDGE_SYNC_WORKER_ENABLED or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="knowledge-sync-worker")
        logger.info("knowledge_sync_worker_started")

    async def stop(self) -> None:
        """Stop polling and wait a bounded time for the worker to exit."""
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=settings.KNOWLEDGE_SYNC_SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        logger.info("knowledge_sync_worker_stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            connector_ids = await self._repository.list_due_connector_ids()
            for connector_id in connector_ids:
                if self._stop.is_set():
                    break
                try:
                    await self._sync_service.sync(connector_id)
                except RuntimeError as error:
                    logger.warning(
                        "scheduled_knowledge_sync_skipped",
                        connector_id=connector_id,
                        error_type=type(error).__name__,
                    )
                except Exception as error:
                    logger.exception(
                        "scheduled_knowledge_sync_failed",
                        connector_id=connector_id,
                        error_type=type(error).__name__,
                    )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=settings.KNOWLEDGE_SYNC_POLL_SECONDS)
            except asyncio.TimeoutError:
                continue


knowledge_sync_worker = KnowledgeSyncWorker(
    KnowledgeSyncRepository(database_service.session_factory),
    knowledge_sync_service,
)
