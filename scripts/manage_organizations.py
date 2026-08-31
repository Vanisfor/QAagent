"""Local administration CLI for organizations, members and knowledge spaces."""

import argparse
import asyncio
import selectors
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.database import database_service  # noqa: E402
from app.services.knowledge import knowledge_service  # noqa: E402

console = Console()


def _selector_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def run(args: argparse.Namespace) -> int:
    """Execute one organization administration operation."""
    try:
        if args.command == "create":
            statement: Any = text(
                """
                INSERT INTO organizations (slug, name) VALUES (:slug, :name)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """
            )
            async with database_service.session_factory() as session, session.begin():
                organization_id = int(
                    (await session.exec(statement, params={"slug": args.slug, "name": args.name})).scalar_one()
                )
            console.print(f"[green]Organization {organization_id} ready.[/green]")
            return 0
        if args.command == "add-member":
            statement = text(
                """
                INSERT INTO organization_members (organization_id, user_id, role)
                SELECT id, :user_id, :role FROM organizations WHERE slug = :slug
                ON CONFLICT (organization_id, user_id) DO UPDATE SET role = EXCLUDED.role
                RETURNING organization_id
                """
            )
            async with database_service.session_factory() as session, session.begin():
                value = (
                    await session.exec(
                        statement,
                        params={"slug": args.slug, "user_id": args.user_id, "role": args.role},
                    )
                ).scalar_one_or_none()
            if value is None:
                raise ValueError("organization was not found")
            console.print("[green]Organization membership saved.[/green]")
            return 0
        if args.command == "create-space":
            query: Any = text("SELECT id FROM organizations WHERE slug = :slug")
            async with database_service.session_factory() as session:
                organization_id = (await session.exec(query, params={"slug": args.organization})).scalar_one_or_none()
            if organization_id is None:
                raise ValueError("organization was not found")
            space_id = await knowledge_service.create_space(
                args.space,
                args.name,
                is_public=args.public,
                owner_user_id=str(args.owner_user_id) if args.owner_user_id is not None else None,
                organization_id=int(organization_id),
            )
            console.print(f"[green]Knowledge space {space_id} ready.[/green]")
            return 0
        raise ValueError(f"unsupported command: {args.command}")
    finally:
        await knowledge_service.close()
        await database_service.engine.dispose()


def parse_args() -> argparse.Namespace:
    """Parse organization administration arguments."""
    parser = argparse.ArgumentParser(description="Manage QAagent organizations and spaces.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("slug")
    create.add_argument("name")
    member = commands.add_parser("add-member")
    member.add_argument("slug")
    member.add_argument("user_id", type=int)
    member.add_argument("--role", choices=["member", "admin", "owner"], default="member")
    space = commands.add_parser("create-space")
    space.add_argument("organization")
    space.add_argument("space")
    space.add_argument("name")
    space.add_argument("--owner-user-id", type=int)
    space.add_argument("--public", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Run the CLI with a psycopg-compatible event loop."""
    args = parse_args()
    loop_factory = _selector_event_loop if sys.platform == "win32" else None
    sys.exit(asyncio.run(run(args), loop_factory=loop_factory))


if __name__ == "__main__":
    main()
