"""Durable current-user profile and settings operations."""

from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.models.user import User
from app.models.user_account import UserProfile, UserSettings, UserPersonalization
from app.schemas.user_account import AppearanceSettings, Personalization, ProfileInput, ProfileResponse, ProfilePatch, AppearancePatch, PersonalizationPatch
from app.services.database import database_service


class UserAccountService:
    """Serialize account mutations on the identity row and key all data by owner."""

    async def ensure_defaults(self, user: User) -> None:
        """Create durable default rows at registration without replacing existing rows."""
        async with database_service.session_factory() as db, db.begin():
            if await db.get(UserProfile, user.id) is None:
                db.add(UserProfile(user_id=user.id, display_name=(user.username or user.email.split("@")[0])[:50]))
            if await db.get(UserSettings, user.id) is None:
                db.add(UserSettings(user_id=user.id))
            if await db.get(UserPersonalization, user.id) is None:
                db.add(UserPersonalization(user_id=user.id))

    async def profile(self, user: User) -> ProfileResponse:
        """Return a profile, with sensible defaults for newly registered users."""
        async with database_service.session_factory() as db:
            row = await db.get(UserProfile, user.id)
            if row is None:
                return ProfileResponse(display_name=(user.username or user.email.split("@")[0])[:50])
            return ProfileResponse.model_validate({"display_name": row.display_name, "bio": row.bio, "language": row.language, "timezone": row.timezone, "avatar_url": f"{settings.API_V1_STR}/users/me/avatar?v={row.avatar_file}" if row.avatar_file else None})

    async def update_profile(self, user: User, payload: ProfilePatch) -> ProfileResponse:
        """Update only editable profile fields under a user-scoped row lock."""
        async with database_service.session_factory() as db, db.begin():
            owner = await db.get(User, user.id, with_for_update=True)
            if owner is None:
                raise HTTPException(404, "User not found")
            row = await db.get(UserProfile, user.id) or UserProfile(user_id=user.id, display_name=(user.username or user.email.split("@")[0])[:50])
            try:
                validated = ProfileInput.model_validate({**row.model_dump(exclude={"user_id", "avatar_file"}), **payload.model_dump(exclude_unset=True)})
            except ValidationError:
                raise HTTPException(422, "Invalid profile fields")
            for key, value in validated.model_dump().items():
                setattr(row, key, value)
            owner.updated_at = datetime.now(UTC)
            db.add(row)
            db.add(owner)
        return await self.profile(user)

    async def appearance(self, user_id: int) -> AppearanceSettings:
        """Read durable appearance, never expose encrypted BYOK columns."""
        async with database_service.session_factory() as db:
            row = await db.get(UserSettings, user_id)
            return AppearanceSettings.model_validate(row.appearance if row else {})

    async def personalization(self, user_id: int) -> Personalization:
        """Read current Agent preferences independently of conversation snapshots."""
        async with database_service.session_factory() as db:
            row = await db.get(UserPersonalization, user_id)
            return Personalization.model_validate(row.preferences if row else {})

    async def save_preferences(self, user_id: int, payload: AppearancePatch | PersonalizationPatch) -> AppearanceSettings | Personalization:
        """Store a validated domain object, retaining existing per-user model secrets."""
        async with database_service.session_factory() as db, db.begin():
            owner = await db.get(User, user_id, with_for_update=True)
            if not owner:
                raise HTTPException(404, "User not found")
            if isinstance(payload, AppearancePatch):
                row = await db.get(UserSettings, user_id) or UserSettings(user_id=user_id)
                try:
                    validated = AppearanceSettings.model_validate({**row.appearance, **payload.model_dump(exclude_unset=True)})
                except ValidationError:
                    raise HTTPException(422, "Invalid appearance fields")
                row.appearance = validated.model_dump()
            else:
                row = await db.get(UserPersonalization, user_id) or UserPersonalization(user_id=user_id)
                try:
                    validated = Personalization.model_validate({**row.preferences, **payload.model_dump(exclude_unset=True)})
                except ValidationError:
                    raise HTTPException(422, "Invalid personalization fields")
                row.preferences = validated.model_dump()
            row.updated_at = datetime.now(UTC)
            db.add(row)
        return validated


user_account_service = UserAccountService()
