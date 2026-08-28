"""Chat session persistence operations."""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import logger
from app.models.session import Session as ChatSession
from app.repositories.checkpoints import CheckpointRepository


class SessionRepository:
    """Persist chat sessions through the shared application connection pool."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        checkpoints: CheckpointRepository,
    ) -> None:
        """Store the shared session factory and checkpoint repository."""
        self._session_factory = session_factory
        self._checkpoints = checkpoints

    async def create(
        self,
        session_id: str,
        user_id: int,
        name: str = "",
        username: str | None = None,
    ) -> ChatSession:
        """Create and return a chat session."""
        async with self._session_factory() as session:
            chat_session = ChatSession(id=session_id, user_id=user_id, name=name, username=username)
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)
            logger.info("session_created", session_id=session_id, user_id=user_id, name=name)
            return chat_session

    async def delete_with_checkpoints(self, session_id: str) -> bool:
        """Atomically delete checkpoint rows and their owning chat session."""
        async with self._session_factory() as session, session.begin():
            chat_session = await session.get(ChatSession, session_id, with_for_update=True)
            if chat_session is None:
                return False
            await self._checkpoints.delete_thread(session, session_id)
            await session.delete(chat_session)

        logger.info("session_and_checkpoints_deleted", session_id=session_id)
        return True

    async def clear_checkpoints(self, session_id: str) -> None:
        """Delete checkpoint rows while retaining the chat session."""
        async with self._session_factory() as session, session.begin():
            await self._checkpoints.delete_thread(session, session_id)

    async def get(self, session_id: str) -> ChatSession | None:
        """Return a chat session by primary key."""
        async with self._session_factory() as session:
            return await session.get(ChatSession, session_id)

    async def list_for_user(self, user_id: int) -> list[ChatSession]:
        """Return all chat sessions owned by a user."""
        async with self._session_factory() as session:
            statement = (
                select(ChatSession).where(col(ChatSession.user_id) == user_id).order_by(col(ChatSession.created_at))
            )
            return list((await session.exec(statement)).all())

    async def update_name(self, session_id: str, name: str) -> ChatSession:
        """Update and return a chat session name."""
        async with self._session_factory() as session:
            chat_session = await session.get(ChatSession, session_id)
            if chat_session is None:
                raise HTTPException(status_code=404, detail="Session not found")
            chat_session.name = name
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)
            logger.info("session_name_updated", session_id=session_id, name=name)
            return chat_session
