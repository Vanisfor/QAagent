"""This file contains the database service for the application."""

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import (
    Environment,
    settings,
)
from app.core.logging import logger
from app.models.session import Session as ChatSession
from app.models.user import User
from app.repositories import CheckpointRepository, SessionRepository, UserRepository


class DatabaseService:
    """Service class for database operations.

    This class handles all database operations for Users, Sessions, and Messages.
    It uses SQLModel for ORM operations and maintains a connection pool.
    """

    def __init__(self):
        """Initialize database service with connection pool."""
        try:
            # Configure environment-specific database connection pool settings
            pool_size = settings.POSTGRES_POOL_SIZE
            max_overflow = settings.POSTGRES_MAX_OVERFLOW

            # Create engine with appropriate pool configuration
            connection_url = (
                f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
                f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
            )

            self.engine = create_async_engine(
                connection_url,
                pool_pre_ping=True,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=30,  # Connection timeout (seconds)
                pool_recycle=1800,  # Recycle connections after 30 minutes
            )
            self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
            checkpoints = CheckpointRepository()
            self.users = UserRepository(self.session_factory)
            self.sessions = SessionRepository(self.session_factory, checkpoints)

            logger.info(
                "database_initialized",
                environment=settings.ENVIRONMENT.value,
                pool_size=pool_size,
                max_overflow=max_overflow,
            )
        except SQLAlchemyError as e:
            logger.error("database_initialization_error", error=str(e), environment=settings.ENVIRONMENT.value)
            # In production, don't raise - allow app to start even with DB issues
            if settings.ENVIRONMENT != Environment.PRODUCTION:
                raise

    async def create_user(self, email: str, password: str, username: str | None = None) -> User:
        """Create a new user.

        Args:
            email: User's email address
            password: Hashed password
            username: Optional display name

        Returns:
            User: The created user
        """
        return await self.users.create(email, password, username)

    async def get_user(self, user_id: int) -> User | None:
        """Get a user by ID.

        Args:
            user_id: The ID of the user to retrieve

        Returns:
            Optional[User]: The user if found, None otherwise
        """
        return await self.users.get(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        """Get a user by email.

        Args:
            email: The email of the user to retrieve

        Returns:
            Optional[User]: The user if found, None otherwise
        """
        return await self.users.get_by_email(email)

    async def delete_user_by_email(self, email: str) -> bool:
        """Delete a user by email.

        Args:
            email: The email of the user to delete

        Returns:
            bool: True if deletion was successful, False if user not found
        """
        return await self.users.delete_by_email(email)

    async def create_session(
        self, session_id: str, user_id: int, name: str = "", username: str | None = None
    ) -> ChatSession:
        """Create a new chat session.

        Args:
            session_id: The ID for the new session
            user_id: The ID of the user who owns the session
            name: Optional name for the session (defaults to empty string)
            username: Display name copied from the user for LLM personalization

        Returns:
            ChatSession: The created session
        """
        return await self.sessions.create(session_id, user_id, name, username)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session by ID.

        Args:
            session_id: The ID of the session to delete

        Returns:
            bool: True if deletion was successful, False if session not found
        """
        return await self.sessions.delete_with_checkpoints(session_id)

    async def clear_checkpoints(self, session_id: str) -> None:
        """Clear LangGraph checkpoints for a session without deleting it."""
        await self.sessions.clear_checkpoints(session_id)

    async def get_session(self, session_id: str) -> ChatSession | None:
        """Get a session by ID.

        Args:
            session_id: The ID of the session to retrieve

        Returns:
            Optional[ChatSession]: The session if found, None otherwise
        """
        return await self.sessions.get(session_id)

    async def get_user_sessions(self, user_id: int) -> list[ChatSession]:
        """Get all sessions for a user.

        Args:
            user_id: The ID of the user

        Returns:
            List[ChatSession]: List of user's sessions
        """
        return await self.sessions.list_for_user(user_id)

    async def update_session_name(self, session_id: str, name: str) -> ChatSession:
        """Update a session's name.

        Args:
            session_id: The ID of the session to update
            name: The new name for the session

        Returns:
            ChatSession: The updated session

        Raises:
            HTTPException: If session is not found
        """
        return await self.sessions.update_name(session_id, name)

    def get_session_maker(self):
        """Get a session maker for creating database sessions.

        Returns:
            Session: A SQLModel session maker
        """
        return self.session_factory

    async def health_check(self) -> bool:
        """Check database connection health.

        Returns:
            bool: True if database is healthy, False otherwise
        """
        try:
            async with self.session_factory() as session:
                # Execute a simple query to check connection
                (await session.exec(select(1))).first()
                return True
        except Exception as e:
            logger.error("database_health_check_failed", error=str(e))
            return False


# Create a singleton instance
database_service = DatabaseService()
