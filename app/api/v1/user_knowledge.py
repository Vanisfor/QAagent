"""Authenticated personal knowledge-space and document endpoints."""

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.user_knowledge import (
    KnowledgeDocumentSummary,
    KnowledgeIngestionJobView,
    KnowledgeSpaceCreate,
    KnowledgeSpaceSummary,
    KnowledgeUploadResponse,
)
from app.services.user_knowledge import user_knowledge_service

router = APIRouter()


@router.get("/knowledge-spaces", response_model=list[KnowledgeSpaceSummary])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def list_spaces(request: Request, user: User = Depends(get_current_user)) -> list[KnowledgeSpaceSummary]:
    """List spaces currently authorized for the authenticated user."""
    del request
    return await user_knowledge_service.list_spaces(user.id)


@router.post("/knowledge-spaces", response_model=KnowledgeSpaceSummary, status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def create_space(
    request: Request,
    payload: KnowledgeSpaceCreate,
    user: User = Depends(get_current_user),
) -> KnowledgeSpaceSummary:
    """Create a private owner-scoped space."""
    del request
    return await user_knowledge_service.create_space(user.id, payload)


@router.get("/knowledge-spaces/{space_slug}/documents", response_model=list[KnowledgeDocumentSummary])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def list_documents(
    request: Request,
    space_slug: str,
    user: User = Depends(get_current_user),
) -> list[KnowledgeDocumentSummary]:
    """List documents after current reader authorization."""
    del request
    return await user_knowledge_service.list_documents(user.id, space_slug)


@router.post("/knowledge-spaces/{space_slug}/documents", response_model=KnowledgeUploadResponse, status_code=202)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["knowledge_upload"][0])
async def upload_document(
    request: Request,
    space_slug: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> KnowledgeUploadResponse:
    """Validate and durably queue one UTF-8 text document."""
    del request
    data = await file.read(settings.KNOWLEDGE_UPLOAD_MAX_BYTES + 1)
    job = await user_knowledge_service.enqueue_upload(
        user.id,
        space_slug,
        filename=file.filename or "document.txt",
        content_type=file.content_type,
        data=data,
    )
    return KnowledgeUploadResponse(job=job)


@router.delete("/knowledge-spaces/{space_slug}/documents/{document_id}", status_code=204)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["knowledge_upload"][0])
async def delete_document(
    request: Request,
    space_slug: str,
    document_id: int,
    user: User = Depends(get_current_user),
) -> Response:
    """Delete one upload after owner/editor authorization."""
    del request
    await user_knowledge_service.delete_document(user.id, space_slug, document_id)
    return Response(status_code=204)


@router.get("/knowledge-ingestion-jobs/{job_id}", response_model=KnowledgeIngestionJobView)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def ingestion_job(
    request: Request,
    job_id: int,
    user: User = Depends(get_current_user),
) -> KnowledgeIngestionJobView:
    """Return one ingestion job only to its authenticated owner."""
    del request
    return await user_knowledge_service.job(user.id, job_id)
