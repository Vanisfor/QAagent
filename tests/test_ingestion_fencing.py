"""Regression coverage for the database write fence after embedding completes."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.core.config import settings
from app.schemas.knowledge import DocumentChunk
from app.services.knowledge import KnowledgeService


def test_reclaimed_job_cannot_write_document_after_embedding(monkeypatch) -> None:
    """The document transaction must reject an old lease before its first mutation."""
    writes = []

    class Cursor:
        async def execute(self, statement, params=None):
            rendered = str(statement)
            if "INSERT INTO knowledge_documents" in rendered:
                writes.append(rendered)
                raise AssertionError("stale worker reached document mutation")

        async def fetchone(self):
            return None

    @asynccontextmanager
    async def open_cursor():
        yield Cursor()

    @asynccontextmanager
    async def open_transaction():
        yield

    class Connection:
        cursor = staticmethod(open_cursor)
        transaction = staticmethod(open_transaction)

    @asynccontextmanager
    async def open_connection():
        yield Connection()

    class Pool:
        connection = staticmethod(open_connection)

    async def pool():
        return Pool()

    async def embed(texts):
        return [[0.0] * settings.EMBEDDING_DIM for _ in texts]

    service = KnowledgeService()
    monkeypatch.setattr(service, "embed", embed)
    monkeypatch.setattr(service, "_get_pool", pool)
    with pytest.raises(RuntimeError, match="lease"):
        asyncio.run(service.replace_source(
            [DocumentChunk(content="private", source="notes.txt")], space_slug="mine",
            source_type="upload", external_id="upload-1", ingestion_lease=(4, "old-token"),
        ))
    assert writes == []
