"""Build provenance-backed graph relations for one knowledge space."""

import argparse
import asyncio
import selectors
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.database import database_service  # noqa: E402
from app.services.knowledge_graph import knowledge_graph_service  # noqa: E402
from app.services.user_llm_settings import user_llm_settings_service  # noqa: E402

console = Console()


def _selector_event_loop() -> asyncio.AbstractEventLoop:
    """Create the Windows-compatible event loop required by async psycopg."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def run(args: argparse.Namespace) -> int:
    """Extract a graph with platform credentials or one user's BYOK runtime."""
    try:
        runtime = await user_llm_settings_service.get_runtime(args.user_id) if args.user_id is not None else None
        relations = await knowledge_graph_service.rebuild_space(args.space, runtime=runtime)
        console.print(f"[green]Built {relations} provenance-backed relation(s) for '{args.space}'.[/green]")
        return 0
    finally:
        await database_service.engine.dispose()


def parse_args() -> argparse.Namespace:
    """Parse graph build arguments."""
    parser = argparse.ArgumentParser(description="Build a provenance-backed knowledge graph.")
    parser.add_argument("--space", default="default-public", help="Knowledge-space slug")
    parser.add_argument("--user-id", type=int, default=None, help="Optional user's configured BYOK runtime")
    return parser.parse_args()


def main() -> None:
    """Run graph extraction with a psycopg-compatible event loop."""
    args = parse_args()
    loop_factory = _selector_event_loop if sys.platform == "win32" else None
    exit_code = asyncio.run(run(args), loop_factory=loop_factory)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
