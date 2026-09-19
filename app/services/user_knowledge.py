"""Owner-scoped knowledge-space and upload operations."""

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from app.core.config import settings
from app.core.metrics import knowledge_acl_denials_total, knowledge_ingestion_jobs_total
from app.repositories.knowledge_ingestion_jobs import KnowledgeIngestionJobRepository
from app.schemas.user_knowledge import (
    KnowledgeDocumentSummary,
    KnowledgeIngestionJobView,
    KnowledgeSpaceCreate,
    KnowledgeSpaceSummary,
)
from app.services.database import database_service
from app.services.knowledge import knowledge_service

_SUPPORTED_MIME = {
    ".md": {"text/markdown", "text/plain"},
    ".txt": {"text/plain"},
    ".rst": {"text/plain", "text/x-rst"},
}
_ROLE_RANK = {"reader": 1, "editor": 2, "owner": 3}
_SLUG_CHARS = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class ValidatedUpload:
    """Validated UTF-8 upload payload used by the durable queue."""

    suffix: str
    text: str
    checksum: str


@dataclass(frozen=True)
class SpaceAuthorization:
    """Server-derived space identity and effective role."""

    id: int
    slug: str
    organization_id: int
    role: str


class UserKnowledgeService:
    """Create private spaces and queue uploads without trusting client ownership."""

    def __init__(self) -> None:
        """Use the shared database pool and durable job repository."""
        self._jobs = KnowledgeIngestionJobRepository(database_service.session_factory)

    @staticmethod
    def validate_upload(
        *, filename: str, content_type: str | None, data: bytes, max_bytes: int
    ) -> ValidatedUpload:
        """Validate extension, MIME, size, UTF-8 and non-empty content."""
        suffix = Path(filename).suffix.lower()
        if suffix not in _SUPPORTED_MIME:
            raise HTTPException(status_code=415, detail="Supported document formats are .md, .txt and .rst")
        if (content_type or "").lower() not in _SUPPORTED_MIME[suffix]:
            raise HTTPException(status_code=415, detail="Document content type does not match its extension")
        if not data or len(data) > max_bytes:
            raise HTTPException(status_code=413 if data else 422, detail="Document is empty or exceeds the size limit")
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise HTTPException(status_code=422, detail="Document must be valid UTF-8 text") from error
        if not decoded.strip():
            raise HTTPException(status_code=422, detail="Document cannot contain only whitespace")
        return ValidatedUpload(suffix=suffix, text=decoded, checksum=hashlib.sha256(data).hexdigest())

    @staticmethod
    def storage_path(root: Path, *, user_id: int, external_id: str, suffix: str) -> Path:
        """Build a server-owned path and verify containment before writing."""
        resolved_root = root.expanduser().resolve()
        path = (resolved_root / str(user_id) / f"{external_id}{suffix}").resolve()
        if not path.is_relative_to(resolved_root):
            raise ValueError("knowledge upload path escaped its configured root")
        return path

    async def create_space(self, user_id: int, payload: KnowledgeSpaceCreate) -> KnowledgeSpaceSummary:
        """Create a private space and owner ACL atomically in the user's organization."""
        base = _SLUG_CHARS.sub("-", (payload.slug or payload.name).strip().lower()).strip("-")[:40]
        if not base:
            base = "knowledge"
        slug = f"u{user_id}-{base}-{uuid4().hex[:8]}"
        async with database_service.session_factory() as session, session.begin():
            membership_statement: Any = text(
                """
                SELECT organization_id FROM organization_members
                WHERE user_id = :user_id
                ORDER BY (organization_id = 1) DESC, organization_id
                LIMIT 1
                """
            )
            organization_id = (
                await session.exec(
                    membership_statement,
                    params={"user_id": user_id},
                )
            ).scalar_one_or_none()
            if organization_id is None:
                raise HTTPException(status_code=403, detail="User has no organization membership")
            insert_space: Any = text(
                """
                INSERT INTO knowledge_spaces (slug, name, is_public, organization_id)
                VALUES (:slug, :name, false, :organization_id)
                RETURNING id
                """
            )
            space_id = (
                await session.exec(
                    insert_space,
                    params={"slug": slug, "name": payload.name, "organization_id": int(organization_id)},
                )
            ).scalar_one()
            insert_owner: Any = text(
                """
                INSERT INTO knowledge_space_principals (space_id, principal_type, principal_id, role)
                VALUES (:space_id, 'user', :user_id, 'owner')
                """
            )
            await session.exec(
                insert_owner,
                params={"space_id": int(space_id), "user_id": str(user_id)},
            )
        return KnowledgeSpaceSummary(slug=slug, name=payload.name, role="owner", is_public=False)

    async def list_spaces(self, user_id: int) -> list[KnowledgeSpaceSummary]:
        """List only spaces visible through current server-side principals."""
        statement: Any = text(
            """
            SELECT space.slug, space.name, space.is_public,
                   COALESCE(direct_acl.role, group_acl.role,
                            CASE WHEN space.is_public THEN 'reader' END) AS role,
                   count(document.id) FILTER (WHERE document.deleted_at IS NULL) AS document_count
            FROM knowledge_spaces AS space
            JOIN organization_members AS member
              ON member.organization_id = space.organization_id AND member.user_id = :user_id
            LEFT JOIN knowledge_space_principals AS direct_acl
              ON direct_acl.space_id = space.id AND direct_acl.principal_type = 'user'
             AND direct_acl.principal_id = :user_principal
            LEFT JOIN LATERAL (
                SELECT acl.role FROM knowledge_space_principals AS acl
                JOIN knowledge_group_members AS group_member
                  ON acl.principal_type = 'group' AND acl.principal_id = group_member.group_id::text
                WHERE acl.space_id = space.id AND group_member.user_id = :user_id
                ORDER BY CASE acl.role WHEN 'owner' THEN 3 WHEN 'editor' THEN 2 ELSE 1 END DESC
                LIMIT 1
            ) AS group_acl ON true
            LEFT JOIN knowledge_documents AS document ON document.space_id = space.id
            WHERE space.is_public OR direct_acl.role IS NOT NULL OR group_acl.role IS NOT NULL
            GROUP BY space.id, direct_acl.role, group_acl.role
            ORDER BY space.name, space.slug
            """
        )
        async with database_service.session_factory() as session:
            rows = (
                await session.exec(statement, params={"user_id": user_id, "user_principal": str(user_id)})
            ).mappings().all()
        return [KnowledgeSpaceSummary.model_validate(dict(row)) for row in rows]

    async def require_role(self, user_id: int, space_slug: str, minimum: str) -> SpaceAuthorization:
        """Resolve current role and reject missing or insufficient authorization."""
        statement: Any = text(
            """
            SELECT space.id, space.slug, space.organization_id, COALESCE(
                (SELECT acl.role FROM knowledge_space_principals AS acl
                 WHERE acl.space_id = space.id AND acl.principal_type = 'user'
                   AND acl.principal_id = :user_principal),
                (SELECT acl.role FROM knowledge_space_principals AS acl
                 JOIN knowledge_group_members AS group_member
                   ON acl.principal_type = 'group' AND acl.principal_id = group_member.group_id::text
                 WHERE acl.space_id = space.id AND group_member.user_id = :user_id
                 ORDER BY CASE acl.role WHEN 'owner' THEN 3 WHEN 'editor' THEN 2 ELSE 1 END DESC LIMIT 1),
                CASE WHEN space.is_public THEN 'reader' END
            ) AS role
            FROM knowledge_spaces AS space
            JOIN organization_members AS member
              ON member.organization_id = space.organization_id AND member.user_id = :user_id
            WHERE space.slug = :space_slug
            """
        )
        async with database_service.session_factory() as session:
            row = (
                await session.exec(
                    statement,
                    params={"user_id": user_id, "user_principal": str(user_id), "space_slug": space_slug},
                )
            ).mappings().first()
        if row is None or row["role"] is None:
            knowledge_acl_denials_total.labels(operation="space_lookup").inc()
            raise HTTPException(status_code=404, detail="Knowledge space not found")
        role = str(row["role"])
        if _ROLE_RANK[role] < _ROLE_RANK[minimum]:
            knowledge_acl_denials_total.labels(operation=f"require_{minimum}").inc()
            raise HTTPException(status_code=403, detail="Insufficient knowledge-space permission")
        return SpaceAuthorization(
            id=int(row["id"]), slug=str(row["slug"]), organization_id=int(row["organization_id"]), role=role
        )

    async def enqueue_upload(
        self,
        user_id: int,
        space_slug: str,
        *,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> KnowledgeIngestionJobView:
        """Validate, persist and durably queue one owner/editor upload."""
        authorization = await self.require_role(user_id, space_slug, "editor")
        validated = self.validate_upload(
            filename=filename,
            content_type=content_type,
            data=data,
            max_bytes=settings.KNOWLEDGE_UPLOAD_MAX_BYTES,
        )
        external_id = uuid4().hex
        path = self.storage_path(
            settings.KNOWLEDGE_UPLOAD_DIR,
            user_id=user_id,
            external_id=external_id,
            suffix=validated.suffix,
        )
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        idempotency_key = hashlib.sha256(
            f"{user_id}:{authorization.id}:{filename}:{validated.checksum}".encode()
        ).hexdigest()
        job_id, created = await self._jobs.enqueue(
            idempotency_key=idempotency_key,
            user_id=user_id,
            space_id=authorization.id,
            space_slug=authorization.slug,
            external_id=external_id,
            original_name=Path(filename).name[:255],
            stored_path=str(path),
            content_type=content_type or "text/plain",
            checksum=validated.checksum,
        )
        if not created:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        job = await self._jobs.get_for_user(job_id, user_id)
        if job is None:
            raise RuntimeError("queued knowledge ingestion job was not found")
        knowledge_ingestion_jobs_total.labels(status="queued" if created else "deduplicated").inc()
        return self._job_view(job)

    async def list_documents(self, user_id: int, space_slug: str) -> list[KnowledgeDocumentSummary]:
        """List active documents after reader authorization."""
        authorization = await self.require_role(user_id, space_slug, "reader")
        statement: Any = text(
            """
            SELECT id, source, title,
                   CASE WHEN deleted_at IS NULL THEN 'available' ELSE 'deleted' END AS status,
                   updated_at
            FROM knowledge_documents
            WHERE space_id = :space_id AND deleted_at IS NULL
            ORDER BY updated_at DESC, id DESC
            """
        )
        async with database_service.session_factory() as session:
            rows = (await session.exec(statement, params={"space_id": authorization.id})).mappings().all()
        return [KnowledgeDocumentSummary.model_validate(dict(row)) for row in rows]

    async def delete_document(self, user_id: int, space_slug: str, document_id: int) -> None:
        """Delete one upload only after owner/editor authorization."""
        await self.require_role(user_id, space_slug, "editor")
        statement: Any = text(
            """
            SELECT document.external_id, job.stored_path
            FROM knowledge_documents AS document
            JOIN knowledge_spaces AS space ON space.id = document.space_id
            LEFT JOIN knowledge_ingestion_jobs AS job
              ON job.document_id = document.id AND job.user_id = :user_id
            WHERE document.id = :document_id AND space.slug = :space_slug
              AND document.source_type = 'upload' AND document.deleted_at IS NULL
            """
        )
        async with database_service.session_factory() as session:
            row = (
                await session.exec(
                    statement,
                    params={"user_id": user_id, "document_id": document_id, "space_slug": space_slug},
                )
            ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Document not found")
        await knowledge_service.delete_source(
            str(row["external_id"]), space_slug=space_slug, source_type="upload"
        )
        if row["stored_path"]:
            path = Path(str(row["stored_path"])).resolve()
            root = settings.KNOWLEDGE_UPLOAD_DIR.expanduser().resolve()
            if path.is_relative_to(root):
                await asyncio.to_thread(path.unlink, missing_ok=True)

    async def job(self, user_id: int, job_id: int) -> KnowledgeIngestionJobView:
        """Return one job only to its owner."""
        job = await self._jobs.get_for_user(job_id, user_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Knowledge ingestion job not found")
        return self._job_view(job)

    @staticmethod
    def _job_view(job: Any) -> KnowledgeIngestionJobView:
        """Remove filesystem paths, checksums and lease state from API output."""
        return KnowledgeIngestionJobView(
            id=int(job.id),
            space_slug=job.space_slug,
            file_name=job.original_name,
            status=job.status,
            attempts=job.attempts,
            document_id=job.document_id,
            error="文档处理失败，请重试；持续失败时请联系管理员。" if job.status == "failed" else None,
            created_at=job.created_at,
        )


user_knowledge_service = UserKnowledgeService()
