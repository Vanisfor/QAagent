"""Register and run durable enterprise knowledge connectors.

Examples:
    uv run python scripts/sync_knowledge.py register-local docs --space default-public --name project-docs
    uv run python scripts/sync_knowledge.py sync 1
    uv run python scripts/sync_knowledge.py reindex --space default-public
"""

import argparse
import asyncio
import os
import selectors
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.database import database_service  # noqa: E402
from app.services.connector_credentials import connector_credential_service  # noqa: E402
from app.services.knowledge import knowledge_service  # noqa: E402
from app.services.knowledge_sync import knowledge_sync_repository, knowledge_sync_service  # noqa: E402
from app.services.external_acl import external_acl_service  # noqa: E402

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


async def _register_remote(args: argparse.Namespace) -> int:
    """Register a provider connector and save its token through the encrypted vault."""
    token = os.getenv(args.token_env, "").strip()
    if not token:
        console.print(f"[red]Environment variable {args.token_env} is empty.[/red]")
        return 1
    if args.command == "register-github":
        kind = "github"
        config = {"owner": args.owner, "repository": args.repository, "branch": args.branch}
    elif args.command == "register-notion":
        kind = "notion"
        config = {"workspace_external_id": args.workspace_external_id}
    elif args.command == "register-sharepoint":
        kind = "sharepoint"
        config = {"drive_id": args.drive_id}
    else:
        raise ValueError(f"unsupported registration command: {args.command}")
    connector_id = await knowledge_sync_repository.create_connector(
        args.space,
        kind,
        args.name,
        config,
        sync_interval_seconds=args.interval,
    )
    await connector_credential_service.save(connector_id, token)
    console.print(f"[green]Registered {kind} connector {connector_id}[/green] ({args.name} → {args.space})")
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


async def _map_user(args: argparse.Namespace) -> int:
    """Bind an external provider user ID to one local organization member."""
    await external_acl_service.map_external_user(args.connector_id, args.external_id, args.local_user_id)
    console.print("[green]External user mapping saved.[/green]")
    return 0


async def _add_group_member(args: argparse.Namespace) -> int:
    """Add a local user to an observed provider group."""
    await external_acl_service.add_external_group_member(
        args.connector_id,
        args.external_group_id,
        args.local_user_id,
    )
    console.print("[green]External group membership saved.[/green]")
    return 0


async def run(args: argparse.Namespace) -> int:
    """Dispatch one administrative connector command and close shared resources."""
    try:
        if args.command == "register-local":
            return await _register_local(args)
        if args.command in {"register-github", "register-notion", "register-sharepoint"}:
            return await _register_remote(args)
        if args.command == "sync":
            return await _sync(args)
        if args.command == "reindex":
            return await _reindex(args)
        if args.command == "map-user":
            return await _map_user(args)
        if args.command == "add-group-member":
            return await _add_group_member(args)
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

    github = commands.add_parser("register-github", help="Register a GitHub repository connector")
    github.add_argument("--owner", required=True)
    github.add_argument("--repository", required=True)
    github.add_argument("--branch", default="main")
    github.add_argument("--space", default="default-public")
    github.add_argument("--name", required=True)
    github.add_argument("--interval", type=int, default=900)
    github.add_argument("--token-env", default="GITHUB_CONNECTOR_TOKEN")

    notion = commands.add_parser("register-notion", help="Register a Notion workspace connector")
    notion.add_argument("--workspace-external-id", required=True)
    notion.add_argument("--space", default="default-public")
    notion.add_argument("--name", required=True)
    notion.add_argument("--interval", type=int, default=900)
    notion.add_argument("--token-env", default="NOTION_CONNECTOR_TOKEN")

    sharepoint = commands.add_parser("register-sharepoint", help="Register a SharePoint drive connector")
    sharepoint.add_argument("--drive-id", required=True)
    sharepoint.add_argument("--space", default="default-public")
    sharepoint.add_argument("--name", required=True)
    sharepoint.add_argument("--interval", type=int, default=900)
    sharepoint.add_argument("--token-env", default="SHAREPOINT_CONNECTOR_TOKEN")

    sync = commands.add_parser("sync", help="Run one registered connector")
    sync.add_argument("connector_id", type=int, help="Connector ID returned by register-local")

    reindex = commands.add_parser("reindex", help="Refresh one space in OpenSearch")
    reindex.add_argument("--space", default="default-public", help="Knowledge-space slug")

    map_user = commands.add_parser("map-user", help="Map a provider user ID to a local user")
    map_user.add_argument("connector_id", type=int)
    map_user.add_argument("external_id")
    map_user.add_argument("local_user_id", type=int)

    group_member = commands.add_parser("add-group-member", help="Add a local user to an external group")
    group_member.add_argument("connector_id", type=int)
    group_member.add_argument("external_group_id")
    group_member.add_argument("local_user_id", type=int)
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
