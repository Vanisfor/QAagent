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
from app.services.connectors.github import GitHubConnector
from app.services.connectors.notion import NotionConnector
from app.services.connectors.sharepoint import SharePointConnector
from app.services.connector_credentials import ConnectorCredentialService, connector_credential_service
from app.services.database import database_service
from app.services.external_acl import ExternalACLService, external_acl_service
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

    async def list_due_connector_ids(self, limit: int = 10) -> list[int]:
        """List connectors due for scheduled synchronization."""
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

    async def reindex_space(self, space_slug: str) -> int:
        """Refresh external lexical ACL documents for a space."""
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

    def __init__(
        self,
        repository: SyncRepository,
        knowledge: KnowledgeWriter,
        credentials: ConnectorCredentialService | None = None,
        external_acl: ExternalACLService | None = None,
    ) -> None:
        """Inject durable state and knowledge write boundaries."""
        self._repository = repository
        self._knowledge = knowledge
        self._credentials = credentials or connector_credential_service
        self._external_acl = external_acl or external_acl_service

    async def _build_connector(self, record: KnowledgeConnectorRecord) -> KnowledgeConnector:
        """Instantiate a connector from its non-secret persisted configuration."""
        if record.kind == "local":
            root_path = record.config.get("root_path")
            if not isinstance(root_path, str) or not root_path:
                raise ValueError("local connector is missing root_path")
            return LocalDirectoryConnector(Path(root_path))
        token = await self._credentials.get(record.id)
        if record.kind == "github":
            return GitHubConnector(
                token=token,
                owner=self._required_config(record, "owner"),
                repository=self._required_config(record, "repository"),
                branch=str(record.config.get("branch") or "main"),
                api_url=str(record.config.get("api_url") or "https://api.github.com"),
            )
        if record.kind == "notion":
            return NotionConnector(
                token=token,
                workspace_external_id=self._required_config(record, "workspace_external_id"),
                api_url=str(record.config.get("api_url") or "https://api.notion.com/v1"),
            )
        if record.kind == "sharepoint":
            return SharePointConnector(
                token=token,
                drive_id=self._required_config(record, "drive_id"),
                api_url=str(record.config.get("api_url") or "https://graph.microsoft.com/v1.0"),
            )
        raise ValueError(f"unsupported knowledge connector kind: {record.kind}")

    @staticmethod
    def _required_config(record: KnowledgeConnectorRecord, key: str) -> str:
        value = record.config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{record.kind} connector is missing {key}")
        return value.strip()

    async def sync(self, connector_id: int) -> KnowledgeSyncResult:
        """Synchronize one connector, including source deletions and cursor settlement."""
        record = await self._repository.get_connector(connector_id)
        if record.status == "disabled":
            raise RuntimeError("knowledge connector is disabled")
        connector = await self._build_connector(record)
        run_id = await self._repository.start_run(record)
        documents_seen = 0
        documents_upserted = 0
        documents_deleted = 0
        chunks_upserted = 0
        next_cursor = record.cursor

        try:
            with trace_span("knowledge.sync", connector_kind=record.kind, connector_id=record.id) as span:
                try:
                    batch = await connector.fetch_changes(record.cursor)
                finally:
                    close = getattr(connector, "close", None)
                    if close is not None:
                        await close()
                documents_seen = len(batch.documents)
                next_cursor = batch.next_cursor
                if batch.has_more:
                    raise RuntimeError("paginated connector batches are not supported by this worker yet")

                await self._external_acl.sync_group_memberships(record.id, batch.group_memberships)

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
                    if record.kind != "local":
                        await self._external_acl.apply_document_acl(
                            record.id,
                            space_slug=record.space_slug,
                            source_type=record.kind,
                            external_id=document.external_id,
                            principals=document.acl_principals,
                        )

                for external_id in batch.deleted_external_ids:
                    deleted = await self._knowledge.delete_source(
                        external_id,
                        space_slug=record.space_slug,
                        source_type=record.kind,
                    )
                    documents_deleted += int(deleted > 0)

                if record.kind != "local" and (documents_upserted or documents_deleted):
                    await self._knowledge.reindex_space(record.space_slug)

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
knowledge_sync_service = KnowledgeSyncService(
    knowledge_sync_repository,
    knowledge_service,
    connector_credential_service,
    external_acl_service,
)
