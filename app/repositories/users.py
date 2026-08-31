"""User persistence operations."""

from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import logger
from app.models.user import User


class UserRepository:
    """Persist users through the shared application connection pool."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Store the shared async session factory."""
        self._session_factory = session_factory

    async def create(self, email: str, password: str, username: str | None = None) -> User:
        """Create and return a user."""
        async with self._session_factory() as session:
            user = User(email=email, hashed_password=password, username=username)
            session.add(user)
            await session.flush()
            membership_statement: Any = text(
                """
                INSERT INTO organization_members (organization_id, user_id, role)
                SELECT id, :user_id, 'member' FROM organizations WHERE slug = 'default'
                ON CONFLICT DO NOTHING
                """
            )
            await session.exec(membership_statement, params={"user_id": user.id})
            await session.commit()
            await session.refresh(user)
            logger.info("user_created", email=email)
            return user

    async def get(self, user_id: int) -> User | None:
        """Return a user by primary key."""
        async with self._session_factory() as session:
            return await session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Return a user by email."""
        async with self._session_factory() as session:
            return (await session.exec(select(User).where(User.email == email))).first()

    async def delete_by_email(self, email: str) -> bool:
        """Delete a user by email when present."""
        async with self._session_factory() as session:
            user = (await session.exec(select(User).where(User.email == email))).first()
            if user is None:
                return False
            await session.delete(user)
            await session.commit()
            logger.info("user_deleted", email=email)
            return True
