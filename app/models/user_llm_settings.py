"""Per-user LLM configuration persisted without plaintext credentials."""

from datetime import (
    UTC,
    datetime,
)
from typing import ClassVar

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    Text,
)
from sqlmodel import Field

from app.models.base import BaseModel


class UserLLMSettings(BaseModel, table=True):
    """Store one active LLM configuration for a user."""

    __tablename__: ClassVar[str] = "user_llm_settings"  # pyright: ignore[reportIncompatibleVariableOverride]

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    )
    provider: str = Field(default="deepseek", max_length=32)
    model: str = Field(max_length=128)
    base_url: str = Field(max_length=2048)
    encrypted_api_key: str = Field(sa_column=Column(Text, nullable=False))
    api_key_last_four: str = Field(max_length=4)
    temperature: float = Field(default=0.2)
    max_tokens: int = Field(default=2000)
    thinking_enabled: bool = Field(default=False)
    validated_at: datetime
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
