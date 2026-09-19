"""Owner-scoped identity, profile, media and Agent preference API."""

from fastapi import APIRouter, Depends, Request, Response, UploadFile, File
from fastapi.responses import FileResponse
from typing import cast

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.user_account import CurrentUser, ProfilePatch, ProfileResponse, AppearanceSettings, Personalization, AppearancePatch, PersonalizationPatch
from app.services.avatars import avatar_service
from app.services.user_account import user_account_service

router = APIRouter()


@router.get("", response_model=CurrentUser)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def current_user(request: Request, user: User = Depends(get_current_user)) -> CurrentUser:
    """Return safe identity information, never a password hash or model secret."""
    return CurrentUser(id=user.id, email=user.email, status=user.status, created_at=user.created_at, last_login_at=user.last_login_at, profile=await user_account_service.profile(user))


@router.get("/profile", response_model=ProfileResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def get_profile(request: Request, user: User = Depends(get_current_user)) -> ProfileResponse:
    """Return the current user's presentation fields."""
    return await user_account_service.profile(user)


@router.patch("/profile", response_model=ProfileResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def patch_profile(request: Request, payload: ProfilePatch, user: User = Depends(get_current_user)) -> ProfileResponse:
    """Update the current profile without a client-selectable user ID."""
    return await user_account_service.update_profile(user, payload)


@router.get("/settings", response_model=AppearanceSettings)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def get_settings(request: Request, user: User = Depends(get_current_user)) -> AppearanceSettings:
    """Return UI configuration; existing /settings/llm endpoints remain intact."""
    return await user_account_service.appearance(user.id)


@router.patch("/settings", response_model=AppearanceSettings)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def patch_settings(request: Request, payload: AppearancePatch, user: User = Depends(get_current_user)) -> AppearanceSettings:
    """Store validated appearance configuration."""
    return cast(AppearanceSettings, await user_account_service.save_preferences(user.id, payload))


@router.get("/personalization", response_model=Personalization)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def get_personalization(request: Request, user: User = Depends(get_current_user)) -> Personalization:
    """Return current Agent preferences."""
    return await user_account_service.personalization(user.id)


@router.patch("/personalization", response_model=Personalization)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def patch_personalization(request: Request, payload: PersonalizationPatch, user: User = Depends(get_current_user)) -> Personalization:
    """Store Agent preferences for subsequent requests."""
    return cast(Personalization, await user_account_service.save_preferences(user.id, payload))


@router.post("/avatar", response_model=ProfileResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def upload_avatar(request: Request, file: UploadFile = File(...), user: User = Depends(get_current_user)) -> ProfileResponse:
    """Validate and persist the current user's avatar."""
    await avatar_service.upload(user, file)
    return await user_account_service.profile(user)


@router.delete("/avatar", status_code=204)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def delete_avatar(request: Request, user: User = Depends(get_current_user)) -> Response:
    """Restore default initials for the current user."""
    await avatar_service.delete(user)
    return Response(status_code=204)


@router.get("/avatar", response_class=FileResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["user_settings"][0])
async def get_avatar(request: Request, user: User = Depends(get_current_user)) -> FileResponse:
    """Serve private media via bearer authentication, never a public directory."""
    return FileResponse(await avatar_service.get_path(user.id), media_type="image/png", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
