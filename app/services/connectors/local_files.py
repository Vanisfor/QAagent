"""Incremental local-directory connector used as the first sync vertical slice."""

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.connectors.base import ConnectorDocument, ConnectorSyncBatch

SUPPORTED_EXTENSIONS = {".md", ".txt", ".rst"}


class LocalDirectoryConnector:
    """Detect local document changes and deletions using content hashes."""

    def __init__(self, root: Path) -> None:
        """Store the connector root without reading it until synchronization."""
        self._root = root.resolve()

    async def fetch_changes(self, cursor: dict[str, Any] | None) -> ConnectorSyncBatch:
        """Scan the directory off the event loop and emit an incremental batch."""
        return await asyncio.to_thread(self._scan, cursor or {})

    def _scan(self, cursor: dict[str, Any]) -> ConnectorSyncBatch:
        """Build a deterministic cursor from supported files under the root."""
        if not self._root.is_dir():
            raise ValueError(f"connector root is not a directory: {self._root}")

        raw_previous = cursor.get("files", {})
        previous = (
            {str(key): str(value) for key, value in raw_previous.items()} if isinstance(raw_previous, dict) else {}
        )
        current: dict[str, str] = {}
        documents: list[ConnectorDocument] = []

        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self._root):
                continue
            external_id = resolved.relative_to(self._root).as_posix()
            content = resolved.read_text(encoding="utf-8", errors="ignore")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            current[external_id] = digest
            if previous.get(external_id) == digest:
                continue

            modified_at = datetime.fromtimestamp(resolved.stat().st_mtime, tz=UTC)
            documents.append(
                ConnectorDocument(
                    external_id=external_id,
                    source=external_id,
                    title=resolved.stem,
                    content=content,
                    content_hash=digest,
                    metadata={
                        "file_name": resolved.name,
                        "relative_path": external_id,
                        "extension": resolved.suffix.lower(),
                    },
                    source_updated_at=modified_at,
                )
            )

        deleted = tuple(sorted(set(previous) - set(current)))
        return ConnectorSyncBatch(
            documents=tuple(documents),
            deleted_external_ids=deleted,
            next_cursor={"version": 1, "files": current},
        )
