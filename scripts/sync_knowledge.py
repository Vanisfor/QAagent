"""Register and run durable enterprise knowledge connectors.

Examples:
    uv run python scripts/sync_knowledge.py register-local docs --space default-public --name project-docs
    uv run python scripts/sync_knowledge.py sync 1
    uv run python scripts/sync_knowledge.py reindex --space default-public
"""

import argparse
import asyncio
import selectors
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.database import database_service  # noqa: E402
from app.services.knowledge import knowledge_service  # noqa: E402
from app.services.knowledge_sync import knowledge_sync_repository, knowledge_sync_service  # noqa: E402

console = Console()


def _selector_event_loop() -> asyncio.AbstractEventLoop:
    """Create the Windows-compatible event loop required by async psycopg."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def _register_local(args: argparse.Namespace) -> int:
    """Register one local-directory connector."""
    root = Path(args.path).resolve()
    if not root.is_dir():
        console.print(f"[red]Connector path is not a directory: {root}[/red]")
        return 1
    connector_id = await knowledge_sync_repository.create_local_connector(args.space, args.name, str(root))
    console.print(f"[green]Registered connector {connector_id}[/green] ({args.name} → {args.space})")
    return 0


async def _sync(args: argparse.Namespace) -> int:
    """Run one connector and print its content-free summary."""
    result = await knowledge_sync_service.sync(args.connector_id)
    table = Table(title=f"Knowledge sync run {result.run_id}")
    table.add_column("Documents seen")
    table.add_column("Upserted")
    table.add_column("Deleted")
    table.add_column("Chunks")
    table.add_row(
        str(result.documents_seen),
        str(result.documents_upserted),
        str(result.documents_deleted),
        str(result.chunks_upserted),
    )
    console.print(table)
    return 0


async def _reindex(args: argparse.Namespace) -> int:
    """Rebuild one space in the configured OpenSearch index."""
    count = await knowledge_service.reindex_space(args.space)
    console.print(f"[green]Reindexed {count} document(s) from '{args.space}'.[/green]")
    return 0


async def run(args: argparse.Namespace) -> int:
    """Dispatch one administrative connector command and close shared resources."""
    try:
        if args.command == "register-local":
            return await _register_local(args)
        if args.command == "sync":
            return await _sync(args)
        if args.command == "reindex":
            return await _reindex(args)
        raise ValueError(f"unsupported command: {args.command}")
    finally:
        await knowledge_service.close()
        await database_service.engine.dispose()


def parse_args() -> argparse.Namespace:
    """Parse connector administration arguments."""
    parser = argparse.ArgumentParser(description="Manage enterprise knowledge synchronization.")
    commands = parser.add_subparsers(dest="command", required=True)

    register = commands.add_parser("register-local", help="Register or update a local-directory connector")
    register.add_argument("path", help="Directory containing .md, .txt or .rst documents")
    register.add_argument("--space", default="default-public", help="Existing destination knowledge-space slug")
    register.add_argument("--name", required=True, help="Unique connector name inside the knowledge space")

    sync = commands.add_parser("sync", help="Run one registered connector")
    sync.add_argument("connector_id", type=int, help="Connector ID returned by register-local")

    reindex = commands.add_parser("reindex", help="Refresh one space in OpenSearch")
    reindex.add_argument("--space", default="default-public", help="Knowledge-space slug")
    return parser.parse_args()


def main() -> None:
    """Run the CLI with a psycopg-compatible event loop."""
    args = parse_args()
    loop_factory = _selector_event_loop if sys.platform == "win32" else None
    try:
        exit_code = asyncio.run(run(args), loop_factory=loop_factory)
    except Exception as error:
        console.print(f"[red]Knowledge sync failed: {type(error).__name__}[/red]")
        raise
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
