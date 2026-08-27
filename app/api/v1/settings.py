"""Authenticated endpoints for per-user LLM configuration."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.user import User
from app.schemas.settings import (
    LLMSettingsInput,
    LLMSettingsResponse,
    LLMSettingsValidationResponse,
)
from app.services.user_llm_settings import (
    UserLLMSettingsError,
    UserLLMSettingsNotConfigured,
    UserLLMSettingsUnavailable,
    UserLLMSettingsValidationError,
    user_llm_settings_service,
)

router = APIRouter()


def _settings_http_error(exc: UserLLMSettingsError) -> HTTPException:
    if isinstance(exc, UserLLMSettingsUnavailable):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, UserLLMSettingsNotConfigured):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, UserLLMSettingsValidationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="用户设置操作失败")


@router.get("/llm", response_model=LLMSettingsResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def get_llm_settings(request: Request, user: User = Depends(get_current_user)) -> LLMSettingsResponse:
    """Return the current user's masked LLM settings."""
    try:
        return await user_llm_settings_service.get_public(user.id)
    except Exception as exc:
        logger.exception("get_user_llm_settings_failed", user_id=user.id, error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail="读取用户模型设置失败")


@router.post("/llm/test", response_model=LLMSettingsValidationResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings_test"][0])
async def test_llm_settings(
    request: Request,
    payload: LLMSettingsInput,
    user: User = Depends(get_current_user),
) -> LLMSettingsValidationResponse:
    """Validate credentials without writing any settings."""
    try:
        runtime = await user_llm_settings_service.validate(user.id, payload)
        return LLMSettingsValidationResponse(model=runtime.model)
    except UserLLMSettingsError as exc:
        raise _settings_http_error(exc)
    except Exception as exc:
        logger.exception("test_user_llm_settings_failed", user_id=user.id, error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail="模型连接测试失败")


@router.put("/llm", response_model=LLMSettingsResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings_test"][0])
async def save_llm_settings(
    request: Request,
    payload: LLMSettingsInput,
    user: User = Depends(get_current_user),
) -> LLMSettingsResponse:
    """Validate credentials and save only after validation succeeds."""
    try:
        return await user_llm_settings_service.save(user.id, payload)
    except UserLLMSettingsError as exc:
        raise _settings_http_error(exc)
    except Exception as exc:
        logger.exception("save_user_llm_settings_failed", user_id=user.id, error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail="保存用户模型设置失败")


@router.delete("/llm", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def delete_llm_settings(request: Request, user: User = Depends(get_current_user)) -> Response:
    """Delete the authenticated user's LLM settings and credential."""
    try:
        await user_llm_settings_service.delete(user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as exc:
        logger.exception("delete_user_llm_settings_failed", user_id=user.id, error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail="删除用户模型设置失败")
