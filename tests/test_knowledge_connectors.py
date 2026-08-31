"""Tests for incremental enterprise knowledge connectors."""

import asyncio
from dataclasses import replace

from app.repositories.knowledge_sync import KnowledgeConnectorRecord
from app.services.connectors.local_files import LocalDirectoryConnector
from app.services.knowledge_sync import KnowledgeSyncService


def test_local_connector_reports_changes_and_tombstones(tmp_path) -> None:
    """The local connector emits only changed files and remembers deletions."""
    first = tmp_path / "first.md"
    second = tmp_path / "second.txt"
    ignored = tmp_path / "image.png"
    first.write_text("first version", encoding="utf-8")
    second.write_text("second document", encoding="utf-8")
    ignored.write_bytes(b"not a document")
    connector = LocalDirectoryConnector(tmp_path)

    initial = asyncio.run(connector.fetch_changes(None))
    unchanged = asyncio.run(connector.fetch_changes(initial.next_cursor))

    first.write_text("first version changed", encoding="utf-8")
    second.unlink()
    changed = asyncio.run(connector.fetch_changes(initial.next_cursor))

    assert {document.external_id for document in initial.documents} == {"first.md", "second.txt"}
    assert initial.deleted_external_ids == ()
    assert unchanged.documents == ()
    assert unchanged.deleted_external_ids == ()
    assert [document.external_id for document in changed.documents] == ["first.md"]
    assert changed.deleted_external_ids == ("second.txt",)
    assert "first.md" in changed.next_cursor["files"]
    assert "second.txt" not in changed.next_cursor["files"]


def test_local_connector_rejects_path_outside_root(tmp_path) -> None:
    """Connector external IDs stay relative to the configured root."""
    missing = tmp_path / "missing"
    connector = LocalDirectoryConnector(missing)

    try:
        asyncio.run(connector.fetch_changes(None))
    except ValueError as error:
        assert "directory" in str(error)
    else:
        raise AssertionError("missing connector root should fail")


def test_sync_service_advances_cursor_after_upserts_and_deletes(tmp_path) -> None:
    """The orchestrator settles a content-free run only after all writes succeed."""
    document = tmp_path / "current.md"
    document.write_text("current enterprise policy", encoding="utf-8")

    class FakeRepository:
        def __init__(self) -> None:
            self.record = KnowledgeConnectorRecord(
                id=3,
                space_slug="private",
                kind="local",
                name="policies",
                config={"root_path": str(tmp_path)},
                cursor={"version": 1, "files": {"deleted.md": "old"}},
                status="idle",
            )
            self.completed: dict | None = None

        async def get_connector(self, connector_id: int):
            return replace(self.record, id=connector_id)

        async def start_run(self, connector):
            return 12

        async def complete_run(self, connector_id, run_id, next_cursor, **stats):
            self.completed = {"connector_id": connector_id, "run_id": run_id, "cursor": next_cursor, **stats}

        async def fail_run(self, connector_id, run_id, error_code):
            raise AssertionError(f"unexpected failure: {error_code}")

    class FakeKnowledge:
        def __init__(self) -> None:
            self.replaced: list[dict] = []
            self.deleted: list[str] = []

        async def replace_source(self, chunks, **kwargs):
            self.replaced.append({"chunks": chunks, **kwargs})
            return len(chunks)

        async def delete_source(self, source, **kwargs):
            self.deleted.append(source)
            return 1

    repository = FakeRepository()
    knowledge = FakeKnowledge()
    result = asyncio.run(KnowledgeSyncService(repository, knowledge).sync(3))  # type: ignore[arg-type]

    assert result.run_id == 12
    assert result.documents_upserted == 1
    assert result.documents_deleted == 1
    assert knowledge.replaced[0]["external_id"] == "current.md"
    assert knowledge.deleted == ["deleted.md"]
    assert repository.completed is not None
    assert repository.completed["cursor"]["files"].keys() == {"current.md"}
