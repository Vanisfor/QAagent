"""Failure-isolated JSONL storage for application traces."""

import json
import os
import queue
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class JsonlTraceWriter:
    """Append compact span records to a daily JSONL file."""

    def __init__(self, directory: Path, retention_days: int) -> None:
        """Initialize the writer and retention policy."""
        self._directory = directory
        self._retention_days = max(1, retention_days)
        self._lock = threading.Lock()
        self._last_cleanup_date: str | None = None
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10000)
        self._worker = threading.Thread(target=self._run, name="trace-writer", daemon=True)
        self._worker.start()

    def write(self, record: dict[str, Any]) -> None:
        """Write one record; callers are responsible for failure isolation."""
        self._queue.put_nowait(record)

    def close(self, timeout: float = 2.0) -> None:
        """Flush queued records and stop the background writer."""
        self._queue.put(None)
        self._worker.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            record = self._queue.get()
            if record is None:
                self._queue.task_done()
                return
            try:
                self._write_sync(record)
            except Exception:
                pass
            finally:
                self._queue.task_done()

    def _write_sync(self, record: dict[str, Any]) -> None:
        today = datetime.now(UTC).date().isoformat()
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._directory.mkdir(parents=True, exist_ok=True)
            if self._last_cleanup_date != today:
                self._cleanup(datetime.now(UTC))
                self._last_cleanup_date = today
            path = self._directory / f"trace-{today}-{os.getpid()}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _cleanup(self, now: datetime) -> None:
        cutoff = now - timedelta(days=self._retention_days)
        for path in self._directory.glob("trace-*.jsonl"):
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                if modified < cutoff:
                    path.unlink()
            except OSError:
                continue
