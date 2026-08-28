"""Durable background jobs for long-term-memory persistence."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import BigInteger, Column, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.models.base import BaseModel


class MemoryJob(BaseModel, table=True):
    """Persist one retryable mem0 write until a worker completes it."""

    __tablename__: ClassVar[str] = "memory_job"  # pyright: ignore[reportIncompatibleVariableOverride]
    __table_args__ = (Index("ix_memory_job_status_available", "status", "available_at"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    idempotency_key: str = Field(unique=True, max_length=64)
    user_id: str = Field(index=True, max_length=64)
    messages: list[dict[str, Any]] = Field(sa_column=Column(JSONB, nullable=False))
    job_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSONB, nullable=False))
    status: str = Field(default="pending", max_length=16)
    attempts: int = Field(default=0)
    available_at: datetime
    locked_at: datetime | None = Field(default=None)
    last_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
