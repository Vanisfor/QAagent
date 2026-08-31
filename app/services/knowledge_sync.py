"""Orchestration for durable incremental enterprise knowledge synchronization."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import trace_span
from app.repositories.knowledge_sync import KnowledgeConnectorRecord, KnowledgeSyncRepository
from app.schemas.knowledge import DocumentChunk
from app.services.connectors.base import ConnectorDocument, KnowledgeConnector
from app.services.connectors.local_files import LocalDirectoryConnector
from app.services.database import database_service
from app.services.knowledge import knowledge_service

_SPLIT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", " ", ""]


class SyncRepository(Protocol):
    """Persistence operations required by the synchronization orchestrator."""

    async def get_connector(self, connector_id: int) -> KnowledgeConnectorRecord:
        """Load one connector."""
        ...

    async def start_run(self, connector: KnowledgeConnectorRecord) -> int:
        """Start one durable synchronization run."""
        ...

    async def complete_run(
        self,
        connector_id: int,
        run_id: int,
        next_cursor: dict[str, Any],
        *,
        documents_seen: int,
        documents_upserted: int,
        documents_deleted: int,
        chunks_upserted: int,
    ) -> None:
        """Complete a run and advance its cursor."""
        ...

    async def fail_run(self, connector_id: int, run_id: int, error_code: str) -> None:
        """Fail a run without advancing its cursor."""
        ...


class KnowledgeWriter(Protocol):
    """Knowledge operations required by connector synchronization."""

    async def replace_source(
        self,
        chunks: list[DocumentChunk],
        *,
        space_slug: str | None = None,
        source_type: str = "local",
        external_id: str | None = None,
        connector_id: int | None = None,
        document_metadata: dict[str, Any] | None = None,
        source_updated_at: datetime | None = None,
    ) -> int:
        """Atomically replace one normalized source document."""
        ...

    async def delete_source(
        self,
        source: str,
        *,
        space_slug: str | None = None,
        source_type: str = "local",
    ) -> int:
        """Delete one normalized source document."""
        ...


@dataclass(frozen=True)
class KnowledgeSyncResult:
    """Content-free summary of one completed connector run."""

    run_id: int
    documents_seen: int
    documents_upserted: int
    documents_deleted: int
    chunks_upserted: int


def _chunk_connector_document(document: ConnectorDocument) -> list[DocumentChunk]:
    """Split a normalized document while preserving connector metadata."""
    if not document.content.strip():
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.KNOWLEDGE_CHUNK_SIZE,
        chunk_overlap=settings.KNOWLEDGE_CHUNK_OVERLAP,
        separators=_SPLIT_SEPARATORS,
        keep_separator=True,
    )
    pieces = [piece for piece in splitter.split_text(document.content) if piece.strip()]
    return [
        DocumentChunk(
            content=piece,
            source=document.source,
            metadata={**document.metadata, "chunk_index": index, "chunk_count": len(pieces)},
        )
        for index, piece in enumerate(pieces)
    ]


class KnowledgeSyncService:
    """Run connector batches and advance cursors only after successful indexing."""

    def __init__(self, repository: SyncRepository, knowledge: KnowledgeWriter) -> None:
        """Inject durable state and knowledge write boundaries."""
        self._repository = repository
        self._knowledge = knowledge

    def _build_connector(self, record: KnowledgeConnectorRecord) -> KnowledgeConnector:
        """Instantiate a connector from its non-secret persisted configuration."""
        if record.kind == "local":
            root_path = record.config.get("root_path")
            if not isinstance(root_path, str) or not root_path:
                raise ValueError("local connector is missing root_path")
            return LocalDirectoryConnector(Path(root_path))
        raise ValueError(f"unsupported knowledge connector kind: {record.kind}")

    async def sync(self, connector_id: int) -> KnowledgeSyncResult:
        """Synchronize one connector, including source deletions and cursor settlement."""
        record = await self._repository.get_connector(connector_id)
        if record.status == "disabled":
            raise RuntimeError("knowledge connector is disabled")
        connector = self._build_connector(record)
        run_id = await self._repository.start_run(record)
        documents_seen = 0
        documents_upserted = 0
        documents_deleted = 0
        chunks_upserted = 0
        next_cursor = record.cursor

        try:
            with trace_span("knowledge.sync", connector_kind=record.kind, connector_id=record.id) as span:
                batch = await connector.fetch_changes(record.cursor)
                documents_seen = len(batch.documents)
                next_cursor = batch.next_cursor
                if batch.has_more:
                    raise RuntimeError("paginated connector batches are not supported by this worker yet")

                for document in batch.documents:
                    chunks = _chunk_connector_document(document)
                    if not chunks:
                        deleted = await self._knowledge.delete_source(
                            document.external_id,
                            space_slug=record.space_slug,
                            source_type=record.kind,
                        )
                        documents_deleted += int(deleted > 0)
                        continue
                    inserted = await self._knowledge.replace_source(
                        chunks,
                        space_slug=record.space_slug,
                        source_type=record.kind,
                        external_id=document.external_id,
                        connector_id=record.id,
                        document_metadata=document.metadata,
                        source_updated_at=document.source_updated_at,
                    )
                    documents_upserted += 1
                    chunks_upserted += inserted

                for external_id in batch.deleted_external_ids:
                    deleted = await self._knowledge.delete_source(
                        external_id,
                        space_slug=record.space_slug,
                        source_type=record.kind,
                    )
                    documents_deleted += int(deleted > 0)

                span.set_attribute("documents_seen", documents_seen)
                span.set_attribute("documents_upserted", documents_upserted)
                span.set_attribute("documents_deleted", documents_deleted)
                span.set_attribute("chunks_upserted", chunks_upserted)

            await self._repository.complete_run(
                record.id,
                run_id,
                next_cursor,
                documents_seen=documents_seen,
                documents_upserted=documents_upserted,
                documents_deleted=documents_deleted,
                chunks_upserted=chunks_upserted,
            )
            logger.info(
                "knowledge_sync_completed",
                connector_id=record.id,
                run_id=run_id,
                documents_upserted=documents_upserted,
                documents_deleted=documents_deleted,
            )
            return KnowledgeSyncResult(
                run_id=run_id,
                documents_seen=documents_seen,
                documents_upserted=documents_upserted,
                documents_deleted=documents_deleted,
                chunks_upserted=chunks_upserted,
            )
        except Exception as error:
            await self._repository.fail_run(record.id, run_id, type(error).__name__)
            logger.exception(
                "knowledge_sync_failed",
                connector_id=record.id,
                run_id=run_id,
                error_type=type(error).__name__,
            )
            raise


knowledge_sync_repository = KnowledgeSyncRepository(database_service.session_factory)
knowledge_sync_service = KnowledgeSyncService(knowledge_sync_repository, knowledge_service)
