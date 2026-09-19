"""Lease-owned worker for durable personal document ingestion."""

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.config import settings
from app.core.logging import logger
from app.core.metrics import knowledge_ingestion_jobs_total
from app.repositories.knowledge_ingestion_jobs import (
    ClaimedKnowledgeIngestionJob,
    KnowledgeIngestionJobRepository,
)
from app.services.database import database_service
from app.services.document_chunking import chunk_document
from app.services.knowledge import KnowledgeIngestionLeaseLost, knowledge_service


class KnowledgeIngestionJobService:
    """Poll and process personal document jobs under renewable leases."""

    def __init__(self) -> None:
        """Initialize without starting background work."""
        self._repository = KnowledgeIngestionJobRepository(database_service.session_factory)
        self._stop = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start one in-process poller."""
        if self._worker is not None and not self._worker.done():
            return
        self._stop.clear()
        self._worker = asyncio.create_task(self._run(), name="knowledge-ingestion-worker")
        logger.info("knowledge_ingestion_worker_started")

    async def stop(self) -> None:
        """Stop after current work or a bounded grace period."""
        self._stop.set()
        if self._worker is None:
            return
        try:
            await asyncio.wait_for(self._worker, timeout=settings.KNOWLEDGE_INGESTION_SHUTDOWN_TIMEOUT)
        except TimeoutError:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        finally:
            self._worker = None
        logger.info("knowledge_ingestion_worker_stopped")

    async def _run(self) -> None:
        """Claim due jobs until shutdown."""
        while not self._stop.is_set():
            try:
                job = await self._repository.claim(settings.KNOWLEDGE_INGESTION_STALE_AFTER_SECONDS)
                if job is None:
                    try:
                        await asyncio.wait_for(
                            self._stop.wait(), timeout=settings.KNOWLEDGE_INGESTION_POLL_SECONDS
                        )
                    except TimeoutError:
                        pass
                    continue
                try:
                    await self._process_with_heartbeat(job)
                except KnowledgeIngestionLeaseLost:
                    logger.warning("knowledge_ingestion_lease_lost", job_id=job.id, attempt=job.attempts)
                except Exception as error:
                    settled = await self._repository.fail(
                        job.id,
                        job.lease_token,
                        job.attempts,
                        settings.KNOWLEDGE_INGESTION_MAX_ATTEMPTS,
                        type(error).__name__,
                    )
                    logger.exception(
                        "knowledge_ingestion_failed",
                        job_id=job.id,
                        attempt=job.attempts,
                        settled=settled,
                        error_type=type(error).__name__,
                    )
                    knowledge_ingestion_jobs_total.labels(
                        status="failed" if job.attempts >= settings.KNOWLEDGE_INGESTION_MAX_ATTEMPTS else "retry"
                    ).inc()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("knowledge_ingestion_worker_iteration_failed", error_type=type(error).__name__)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=settings.KNOWLEDGE_INGESTION_POLL_SECONDS)
                except TimeoutError:
                    pass

    async def _process_with_heartbeat(self, job: ClaimedKnowledgeIngestionJob) -> None:
        """Process one upload while proving lease ownership."""
        heartbeat_seconds = max(
            0.1,
            min(
                settings.KNOWLEDGE_INGESTION_HEARTBEAT_SECONDS,
                settings.KNOWLEDGE_INGESTION_STALE_AFTER_SECONDS / 3,
            ),
        )
        processing = asyncio.create_task(self._ingest(job))
        try:
            while not processing.done():
                done, _ = await asyncio.wait({processing}, timeout=heartbeat_seconds)
                if processing in done:
                    break
                if not await self._repository.renew(job.id, job.lease_token):
                    processing.cancel()
                    await asyncio.gather(processing, return_exceptions=True)
                    raise KnowledgeIngestionLeaseLost(f"lease lost for job {job.id}")
            document_id = await processing
            if not await self._repository.succeed(job.id, job.lease_token, document_id):
                raise KnowledgeIngestionLeaseLost(f"lease lost while completing job {job.id}")
            logger.info("knowledge_ingestion_completed", job_id=job.id, document_id=document_id)
            knowledge_ingestion_jobs_total.labels(status="completed").inc()
        finally:
            if not processing.done():
                processing.cancel()
                await asyncio.gather(processing, return_exceptions=True)

    async def _ingest(self, job: ClaimedKnowledgeIngestionJob) -> int:
        """Read, chunk, embed and index one validated stored upload."""
        path = Path(job.stored_path).resolve()
        root = settings.KNOWLEDGE_UPLOAD_DIR.expanduser().resolve()
        expected_owner_root = (root / str(job.user_id)).resolve()
        if not path.is_relative_to(expected_owner_root):
            raise ValueError("stored upload path is outside its owner directory")
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        chunks = chunk_document(
            content,
            source=job.original_name,
            chunk_size=settings.KNOWLEDGE_CHUNK_SIZE,
            chunk_overlap=settings.KNOWLEDGE_CHUNK_OVERLAP,
            document_title=Path(job.original_name).stem,
            format_hint=Path(job.original_name).suffix.lower(),
            metadata={"file_name": job.original_name},
        )
        if not chunks:
            raise ValueError("document produced no indexable chunks")
        await knowledge_service.replace_source(
            chunks,
            space_slug=job.space_slug,
            source_type="upload",
            external_id=job.external_id,
            document_metadata={"owner_user_id": job.user_id, "file_name": job.original_name},
            ingestion_lease=(job.id, job.lease_token),
        )
        return await self._document_id(job)

    @staticmethod
    async def _document_id(job: ClaimedKnowledgeIngestionJob) -> int:
        """Resolve the document created by the exact space/type/external identity."""
        statement: Any = text(
            """
            SELECT document.id
            FROM knowledge_documents AS document
            JOIN knowledge_spaces AS space ON space.id = document.space_id
            WHERE space.id = :space_id AND document.source_type = 'upload'
              AND document.external_id = :external_id AND document.deleted_at IS NULL
            """
        )
        async with database_service.session_factory() as session:
            document_id = (
                await session.exec(
                    statement,
                    params={"space_id": job.space_id, "external_id": job.external_id},
                )
            ).scalar_one_or_none()
        if document_id is None:
            raise RuntimeError("ingested document identifier was not found")
        return int(document_id)


knowledge_ingestion_job_service = KnowledgeIngestionJobService()
