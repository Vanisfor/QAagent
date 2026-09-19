"""Validated local media storage behind authenticated current-user endpoints."""

import io
from pathlib import Path
from uuid import uuid4

from anyio import to_thread
from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.logging import logger
from app.models.user import User
from app.models.user_account import UserProfile
from app.services.database import database_service

MAX_AVATAR_BYTES = 2 * 1024 * 1024
FORMATS = {"image/png": ("PNG", {".png"}), "image/jpeg": ("JPEG", {".jpg", ".jpeg"}), "image/webp": ("WEBP", {".webp"})}


class AvatarService:
    """Normalize uploads to a small PNG and never trust client paths."""

    def path(self, filename: str) -> Path:
        """Resolve generated filenames within the configured storage root."""
        if len(filename) != 36 or not filename.endswith(".png") or any(char not in "0123456789abcdef" for char in filename[:-4]):
            raise HTTPException(404, "Avatar not found")
        return settings.USER_MEDIA_DIR.resolve() / filename

    def normalize(self, data: bytes, mime: str, filename: str) -> bytes:
        """Check size, MIME, extension, actual format and decoded dimensions."""
        if mime not in FORMATS or Path(filename).suffix.lower() not in FORMATS[mime][1]:
            raise HTTPException(422, "Use JPG, PNG or WebP images")
        if not data or len(data) > MAX_AVATAR_BYTES:
            raise HTTPException(422, "Avatar must be at most 2 MB")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != FORMATS[mime][0] or image.width * image.height > 16_000_000:
                    raise HTTPException(422, "Invalid image format or dimensions")
                image.load()
                image.thumbnail((512, 512))
                output = io.BytesIO()
                image.convert("RGBA").save(output, format="PNG")
                return output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(422, "Invalid image file")

    async def upload(self, user: User, file: UploadFile) -> None:
        """Write normalized content and clean up newly created files on rollback."""
        data = await file.read(MAX_AVATAR_BYTES + 1)
        normalized = await to_thread.run_sync(self.normalize, data, file.content_type or "", file.filename or "")
        filename = f"{uuid4().hex}.png"
        path = self.path(filename)
        await to_thread.run_sync(lambda: path.parent.mkdir(parents=True, exist_ok=True))
        await to_thread.run_sync(path.write_bytes, normalized)
        try:
            await self._replace(user, filename)
        except Exception:
            await to_thread.run_sync(lambda: path.unlink(missing_ok=True))
            raise

    async def _replace(self, user: User, filename: str | None) -> None:
        """Atomically replace the owner's reference, then remove the old file."""
        async with database_service.session_factory() as db, db.begin():
            await db.get(User, user.id, with_for_update=True)
            row = await db.get(UserProfile, user.id) or UserProfile(user_id=user.id, display_name=(user.username or user.email.split("@")[0])[:50])
            old = row.avatar_file
            row.avatar_file = filename
            db.add(row)
        if old:
            try:
                await to_thread.run_sync(lambda: self.path(old).unlink(missing_ok=True))
            except OSError:
                logger.exception("old_avatar_cleanup_failed", user_id=user.id)

    async def delete(self, user: User) -> None:
        """Remove the avatar and restore the default initials."""
        await self._replace(user, None)

    async def get_path(self, user_id: int) -> Path:
        """Resolve only the requesting user's current avatar."""
        async with database_service.session_factory() as db:
            row = await db.get(UserProfile, user_id)
            if not row or not row.avatar_file:
                raise HTTPException(404, "Avatar not found")
            path = self.path(row.avatar_file)
        if not await to_thread.run_sync(path.is_file):
            raise HTTPException(404, "Avatar not found")
        return path


avatar_service = AvatarService()
