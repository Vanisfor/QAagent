"""Tests for scheduled connector background dispatch."""

import asyncio

from app.core.config import settings
from app.services.knowledge_sync_worker import KnowledgeSyncWorker


def test_background_worker_dispatches_due_connector(monkeypatch) -> None:
    """The scheduler polls due IDs and exits cleanly after a stop signal."""
    calls: list[int] = []

    class FakeRepository:
        async def list_due_connector_ids(self, limit: int = 10) -> list[int]:
            return [9] if not calls else []

    class FakeSync:
        worker: KnowledgeSyncWorker

        async def sync(self, connector_id: int) -> None:
            calls.append(connector_id)
            self.worker._stop.set()  # noqa: SLF001

    async def run() -> None:
        sync = FakeSync()
        worker = KnowledgeSyncWorker(FakeRepository(), sync)  # type: ignore[arg-type]
        sync.worker = worker
        await worker.start()
        if worker._task is not None:  # noqa: SLF001
            await worker._task  # noqa: SLF001
        await worker.stop()

    monkeypatch.setattr(settings, "KNOWLEDGE_SYNC_WORKER_ENABLED", True)
    monkeypatch.setattr(settings, "KNOWLEDGE_SYNC_POLL_SECONDS", 0.01)
    asyncio.run(run())

    assert calls == [9]
