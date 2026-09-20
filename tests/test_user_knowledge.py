"""Tests for owner-scoped personal knowledge uploads."""

from pathlib import Path
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import asyncio
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.user_knowledge import KnowledgeSpaceCreate
from app.services.user_knowledge import UserKnowledgeService
from app.services.user_knowledge import SpaceAuthorization
from app.core.config import settings


def test_space_create_contract_rejects_client_identity_and_public_flag() -> None:
    """Ownership and visibility come only from authenticated server context."""
    with pytest.raises(ValidationError):
        KnowledgeSpaceCreate.model_validate({"name": "Mine", "user_id": 9})
    with pytest.raises(ValidationError):
        KnowledgeSpaceCreate.model_validate({"name": "Mine", "is_public": True})


def test_space_name_cannot_be_only_whitespace() -> None:
    """Validation must reject the empty value after trimming."""
    with pytest.raises(ValidationError):
        KnowledgeSpaceCreate(name="   ")


def test_job_status_never_exposes_provider_error_or_storage_paths() -> None:
    """Persisted exceptions can include credentials and filenames; the API uses safe copy."""
    job = SimpleNamespace(id=1, space_slug="mine", original_name="notes.txt", status="failed",
                          attempts=5, document_id=None, created_at=datetime.now(UTC),
                          last_error="provider token secret /private/path")
    result = UserKnowledgeService._job_view(job)
    assert "secret" not in result.model_dump_json()
    assert "/private/path" not in result.model_dump_json()


def test_upload_validation_accepts_supported_utf8_text() -> None:
    """The v1 upload boundary accepts only the documented text formats."""
    upload = UserKnowledgeService.validate_upload(
        filename="notes.md",
        content_type="text/markdown",
        data="# Notes\nGrounded fact".encode(),
        max_bytes=1024,
    )

    assert upload.suffix == ".md"
    assert upload.text.startswith("# Notes")


@pytest.mark.parametrize(
    ("filename", "content_type", "data"),
    [
        ("notes.pdf", "application/pdf", b"pdf"),
        ("notes.md", "application/pdf", b"text"),
        ("notes.txt", "text/plain", b"\xff"),
        ("notes.rst", "text/plain", b""),
    ],
)
def test_upload_validation_rejects_unsupported_or_invalid_content(
    filename: str, content_type: str, data: bytes
) -> None:
    """Extension, MIME, UTF-8 and non-empty checks all fail closed."""
    with pytest.raises(HTTPException):
        UserKnowledgeService.validate_upload(
            filename=filename,
            content_type=content_type,
            data=data,
            max_bytes=1024,
        )


def test_upload_storage_path_cannot_escape_owner_directory(tmp_path: Path) -> None:
    """Client filenames never participate in the persisted filesystem path."""
    path = UserKnowledgeService.storage_path(tmp_path, user_id=7, external_id="abc123", suffix=".md")

    assert path == (tmp_path / "7" / "abc123.md").resolve()
    assert path.is_relative_to(tmp_path.resolve())
    assert ".." not in path.parts


def test_failed_job_enqueue_removes_just_written_upload(monkeypatch, tmp_path: Path) -> None:
    """A database error must not strand a raw private document on disk."""
    service = UserKnowledgeService()
    monkeypatch.setattr(settings, "KNOWLEDGE_UPLOAD_DIR", tmp_path)

    async def authorize(_user_id, _slug, _role):
        return SpaceAuthorization(id=1, slug="mine", organization_id=1, role="owner")

    class FailedQueue:
        async def enqueue(self, **_values):
            raise RuntimeError("database unavailable")

    class EmptySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def exec(self, *_args, **_kwargs):
            class Result:
                @staticmethod
                def scalar_one():
                    return 0
            return Result()

    monkeypatch.setattr(service, "require_role", authorize)
    monkeypatch.setattr(service, "_jobs", FailedQueue())
    monkeypatch.setattr("app.services.user_knowledge.database_service.session_factory", lambda: EmptySession())
    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(service.enqueue_upload(
            7, "mine", filename="notes.txt", content_type="text/plain", data=b"private document"
        ))
    assert list((tmp_path / "7").glob("*")) == []
