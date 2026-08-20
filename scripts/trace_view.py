"""Render one local JSONL trace as a compact terminal tree."""

import argparse
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.tree import Tree


def load_trace(directory: Path, trace_id: str) -> list[dict[str, Any]]:
    """Load all spans belonging to a trace ID."""
    spans: list[dict[str, Any]] = []
    for path in sorted(directory.glob("trace-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("trace_id") == trace_id:
                spans.append(record)
    return spans


def render_trace(spans: list[dict[str, Any]]) -> Tree:
    """Build a Rich tree from flat span records."""
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for span in spans:
        by_parent.setdefault(span.get("parent_span_id"), []).append(span)

    root = Tree("trace")

    def add(parent: Tree, span: dict[str, Any]) -> None:
        status = span.get("status", "unknown")
        duration = span.get("duration_ms", 0)
        node = parent.add(f"{span.get('name')}  {duration} ms  [{status}]")
        for child in by_parent.get(span.get("span_id"), []):
            add(node, child)

    span_ids = {span.get("span_id") for span in spans}
    roots = [span for span in spans if span.get("parent_span_id") not in span_ids]
    for span in roots:
        add(root, span)
    return root


def main() -> None:
    """Run the trace viewer CLI."""
    parser = argparse.ArgumentParser(description="View a local trace tree")
    parser.add_argument("trace_id")
    parser.add_argument("--trace-dir", default="logs/traces")
    args = parser.parse_args()
    spans = load_trace(Path(args.trace_dir), args.trace_id)
    console = Console()
    if not spans:
        console.print(f"[yellow]Trace not found: {args.trace_id}[/yellow]")
        return
    console.print(render_trace(spans))


if __name__ == "__main__":
    main()
