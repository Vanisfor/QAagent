"""Durable owner-scoped jobs for personal document ingestion."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import BigInteger, Column, ForeignKey, Index, Text
from sqlmodel import Field

from app.models.base import BaseModel


class KnowledgeIngestionJob(BaseModel, table=True):
    """Persist one retryable upload until indexing settles under a lease."""

    __tablename__: ClassVar[str] = "knowledge_ingestion_jobs"  # pyright: ignore[reportIncompatibleVariableOverride]
    __table_args__ = (
        Index("ix_knowledge_ingestion_status_available", "status", "available_at"),
        Index("ix_knowledge_ingestion_user_created", "user_id", "created_at"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    idempotency_key: str = Field(unique=True, max_length=64)
    user_id: int = Field(foreign_key="user.id", index=True)
    space_id: int = Field(sa_column=Column(BigInteger, ForeignKey("knowledge_spaces.id", ondelete="CASCADE")))
    space_slug: str = Field(max_length=128)
    external_id: str = Field(max_length=64)
    original_name: str = Field(max_length=255)
    stored_path: str = Field(max_length=512)
    content_type: str = Field(max_length=100)
    checksum: str = Field(max_length=64)
    status: str = Field(default="pending", max_length=16)
    attempts: int = Field(default=0)
    available_at: datetime
    locked_at: datetime | None = Field(default=None)
    lease_token: str | None = Field(default=None, max_length=32)
    document_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("knowledge_documents.id", ondelete="SET NULL"), nullable=True),
    )
    last_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
