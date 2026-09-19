"""Separate user presentation, preferences and revocable login sessions."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class UserProfile(SQLModel, table=True):
    """Profile keyed by the existing user identity."""

    __tablename__: ClassVar[str] = "user_profiles"  # pyright: ignore[reportIncompatibleVariableOverride]
    user_id: int = Field(primary_key=True, foreign_key="user.id", ondelete="CASCADE")
    display_name: str = Field(max_length=50)
    avatar_file: str | None = Field(default=None, max_length=64)
    bio: str = Field(default="", max_length=500)
    language: str = Field(default="auto", max_length=8)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)


class UserSettings(SQLModel, table=True):
    """Durable appearance configuration, separate from encrypted model secrets."""

    __tablename__: ClassVar[str] = "user_settings"  # pyright: ignore[reportIncompatibleVariableOverride]
    user_id: int = Field(primary_key=True, foreign_key="user.id", ondelete="CASCADE")
    appearance: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_column=Column(DateTime(timezone=True), nullable=False))


class UserPersonalization(SQLModel, table=True):
    """Durable Agent preferences without introducing a second user identity."""

    __tablename__: ClassVar[str] = "user_personalization"  # pyright: ignore[reportIncompatibleVariableOverride]
    user_id: int = Field(primary_key=True, foreign_key="user.id", ondelete="CASCADE")
    preferences: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_column=Column(DateTime(timezone=True), nullable=False))


class UserLoginSession(SQLModel, table=True):
    """Login session distinct from existing chat Session/checkpoint ownership."""

    __tablename__: ClassVar[str] = "user_sessions"  # pyright: ignore[reportIncompatibleVariableOverride]
    id: str = Field(primary_key=True, max_length=36)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    refresh_token_hash: str = Field(max_length=64)
    device: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
