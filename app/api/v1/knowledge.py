"""Authenticated Research and Wiki knowledge-product endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.session import Session
from app.schemas.knowledge import RetrievalContext
from app.schemas.workflows import KnowledgeWorkflowRequest, ResearchResponse, WikiResponse
from app.services.knowledge_workflows import KnowledgeEvidenceNotFound, knowledge_workflow_service
from app.services.user_llm_settings import (
    UserLLMSettingsNotConfigured,
    UserLLMSettingsUnavailable,
    user_llm_settings_service,
)

router = APIRouter()


async def _workflow_runtime(user_id: int):
    """Resolve BYOK runtime and translate safe configuration failures."""
    try:
        return await user_llm_settings_service.get_runtime(user_id)
    except UserLLMSettingsNotConfigured as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except UserLLMSettingsUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/research", response_model=ResearchResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["research"][0])
async def create_research_report(
    request: Request,
    payload: KnowledgeWorkflowRequest,
    session: Session = Depends(get_current_session),
) -> ResearchResponse:
    """Generate an evidence-backed research report for the authenticated user."""
    del request
    runtime = await _workflow_runtime(session.user_id)
    context = RetrievalContext(user_id=str(session.user_id), space_slugs=tuple(payload.space_slugs))
    try:
        return await knowledge_workflow_service.research(payload.query, context, runtime=runtime)
    except KnowledgeEvidenceNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        logger.exception("research_workflow_failed", user_id=session.user_id, error_type=type(error).__name__)
        raise HTTPException(status_code=500, detail="Research generation failed. Use X-Request-ID for support.")


@router.post("/wiki", response_model=WikiResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["wiki"][0])
async def create_wiki_page(
    request: Request,
    payload: KnowledgeWorkflowRequest,
    session: Session = Depends(get_current_session),
) -> WikiResponse:
    """Generate an evidence-backed Wiki page for the authenticated user."""
    del request
    runtime = await _workflow_runtime(session.user_id)
    context = RetrievalContext(user_id=str(session.user_id), space_slugs=tuple(payload.space_slugs))
    try:
        return await knowledge_workflow_service.wiki(payload.query, context, runtime=runtime)
    except KnowledgeEvidenceNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        logger.exception("wiki_workflow_failed", user_id=session.user_id, error_type=type(error).__name__)
        raise HTTPException(status_code=500, detail="Wiki generation failed. Use X-Request-ID for support.")
