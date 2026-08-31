"""Ingest local documents into the pgvector knowledge base for RAG Q&A.

Usage (from the project root):

    uv run python scripts/ingest_docs.py docs/                        # ingest a directory
    uv run python scripts/ingest_docs.py notes/guide.md               # ingest one file
    uv run python scripts/ingest_docs.py docs/ --reset                # wipe KB first
    uv run python scripts/ingest_docs.py docs/ --chunk-size 500       # tune chunking

Supported formats: .md, .txt, .rst. Requires:
  - PostgreSQL running with pgvector (make docker-up / make docker-migrate)
  - SILICONFLOW_API_KEY set in .env.development (free embedding API)

Each source is replaced atomically: re-ingesting an edited file removes stale
chunks, while an embedding failure leaves the previously searchable version
untouched.
"""

import argparse
import asyncio
import selectors
import sys
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# This script is a standalone CLI, not part of the app package, so we add the
# project root to sys.path to import ``app`` (equivalent to ``python -m``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.logging import logger  # noqa: E402
from app.schemas.knowledge import DocumentChunk  # noqa: E402
from app.services.knowledge import knowledge_service  # noqa: E402

console = Console()

SUPPORTED_EXTENSIONS = {".md", ".txt", ".rst"}

# Chinese-aware separators so paragraphs and sentences split cleanly.
SPLIT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", " ", ""]


def _selector_event_loop() -> asyncio.AbstractEventLoop:
    """Create the Windows-compatible event loop required by async psycopg."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def collect_files(path: Path) -> list[Path]:
    """Collect supported document files from a file path or a directory."""
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            console.print(f"[yellow]Skipping unsupported file: {path}[/yellow]")
            return []
        return [path]

    files = [p for p in sorted(path.rglob("*")) if p.suffix.lower() in SUPPORTED_EXTENSIONS]
    return files


def chunk_file(path: Path, chunk_size: int, chunk_overlap: int, source: str | None) -> list[DocumentChunk]:
    """Read a file and split it into overlapping chunks."""
    if chunk_overlap >= chunk_size:
        raise ValueError(f"chunk_overlap ({chunk_overlap}) must be smaller than chunk_size ({chunk_size})")

    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=SPLIT_SEPARATORS,
        keep_separator=True,
    )
    pieces = splitter.split_text(text)

    resolved_source = source or path.name
    non_empty_pieces = [piece for piece in pieces if piece.strip()]
    return [
        DocumentChunk(
            content=piece,
            source=resolved_source,
            metadata={
                "file_name": path.name,
                "chunk_index": index,
                "chunk_count": len(non_empty_pieces),
            },
        )
        for index, piece in enumerate(non_empty_pieces)
    ]


async def run(args: argparse.Namespace) -> int:
    """Execute the ingestion pipeline."""
    target = Path(args.path).resolve()
    if not target.exists():
        console.print(f"[red]Path not found: {target}[/red]")
        return 1

    files = collect_files(target)
    if not files:
        console.print("[yellow]No supported documents found (.md, .txt, .rst).[/yellow]")
        return 1
    if args.source and len(files) > 1:
        console.print("[red]--source can only be used when ingesting one file.[/red]")
        return 1

    if args.reset:
        confirm = input(f"WARNING: this will DELETE all chunks in '{settings.KNOWLEDGE_TABLE}'. Continue? [y/N] ")
        if confirm.strip().lower() not in ("y", "yes"):
            console.print("[yellow]Aborted.[/yellow]")
            return 0
        deleted = await knowledge_service.reset(space_slug=args.space)
        console.print(f"[cyan]Knowledge base reset: removed {deleted} chunks.[/cyan]")

    total_chunks = 0
    total_files = 0
    failed: list[Path] = []
    table = Table(title="Ingestion summary")
    table.add_column("File")
    table.add_column("Chunks")
    table.add_column("Status")

    try:
        with console.status("[bold green]Embedding and storing documents...[/bold green]"):
            for file_path in files:
                try:
                    source = args.source
                    if source is None:
                        source = file_path.relative_to(target).as_posix() if target.is_dir() else file_path.name
                    chunks = chunk_file(file_path, args.chunk_size, args.chunk_overlap, source)
                    if not chunks:
                        await knowledge_service.delete_source(source, space_slug=args.space)
                        continue
                    inserted = await knowledge_service.replace_source(chunks, space_slug=args.space)
                    total_chunks += inserted
                    total_files += 1
                    table.add_row(str(file_path), str(inserted), "ok")
                    logger.info("document_ingested", file=str(file_path), chunks=inserted)
                except Exception as e:
                    failed.append(file_path)
                    table.add_row(str(file_path), "-", f"[red]failed: {e}[/red]")
                    logger.exception("document_ingestion_failed", file=str(file_path), error=str(e))
    finally:
        # Close the pool so psycopg_pool worker threads stop cleanly before
        # asyncio.run tears down the event loop.
        await knowledge_service.close()

    console.print(table)
    if failed:
        console.print(f"[yellow]Finished with {len(failed)} failed file(s).[/yellow]")
        for file_path in failed:
            console.print(f"  [red]- {file_path}[/red]")
    console.print(
        Panel.fit(
            f"[bold green]Done![/bold green] {total_files} documents, {total_chunks} chunks "
            f"stored in space '{args.space}' / table '{settings.KNOWLEDGE_TABLE}' "
            f"(model: {settings.EMBEDDING_MODEL})."
        )
    )
    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Ingest documents into the RAG knowledge base.")
    parser.add_argument("path", help="Path to a document file or a directory of documents")
    parser.add_argument(
        "--chunk-size", type=int, default=settings.KNOWLEDGE_CHUNK_SIZE, help="Chunk size (characters)"
    )
    parser.add_argument("--chunk-overlap", type=int, default=settings.KNOWLEDGE_CHUNK_OVERLAP, help="Chunk overlap")
    parser.add_argument("--source", type=str, default=None, help="Override the source label for all chunks")
    parser.add_argument(
        "--space",
        type=str,
        default=settings.KNOWLEDGE_DEFAULT_SPACE,
        help="Existing knowledge-space slug (default: default-public)",
    )
    parser.add_argument("--reset", action="store_true", help="Delete all existing chunks before ingesting")
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()
    loop_factory = _selector_event_loop if sys.platform == "win32" else None
    exit_code = asyncio.run(run(args), loop_factory=loop_factory)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
