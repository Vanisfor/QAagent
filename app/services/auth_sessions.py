"""Revocable login sessions and refresh-token rotation in PostgreSQL."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import select

from app.core.config import settings
from app.models.user import User
from app.models.user_account import UserLoginSession
from app.schemas.auth import Token
from app.services.database import database_service
from app.utils.auth import create_access_token, decode_access_token


class AuthSessionService:
    """Bind access and conversation credentials to one revocable login."""

    @staticmethod
    def hash_refresh(value: str) -> str:
        """Hash a high-entropy random refresh secret; passwords still use bcrypt."""
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def token(subject: str, purpose: str, sid: str, user_id: int) -> Token:
        """Issue short-lived access credentials with explicit purpose and owner."""
        return create_access_token(subject, purpose, timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES), login_session_id=sid, user_id=user_id)

    async def create(self, user: User, device: str) -> tuple[Token, str]:
        """Create a login and update last-login timestamp."""
        sid = str(uuid4())
        refresh = f"{sid}.{secrets.token_urlsafe(48)}"
        async with database_service.session_factory() as db:
            row = UserLoginSession(id=sid, user_id=user.id, refresh_token_hash=self.hash_refresh(refresh), device=device[:200], expires_at=datetime.now(UTC) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS))
            db.add(row)
            stored_user = await db.get(User, user.id)
            if stored_user is None or stored_user.status != "active":
                raise HTTPException(401, "Account unavailable")
            stored_user.last_login_at = datetime.now(UTC)
            db.add(stored_user)
            await db.commit()
        return self.token(str(user.id), "user", sid, user.id), refresh

    async def authenticate(self, token: str, purpose: str) -> tuple[User, str, str]:
        """Reject revoked, expired, disabled and unbound legacy credentials."""
        claims = decode_access_token(token, purpose)
        if not claims or not claims.get("sid") or not str(claims.get("uid", "")).isdigit():
            raise HTTPException(401, "Invalid authentication credentials")
        uid = int(claims["uid"])
        async with database_service.session_factory() as db:
            login = await db.get(UserLoginSession, claims["sid"])
            user = await db.get(User, uid)
            if not login or login.user_id != uid or login.revoked_at or login.expires_at <= datetime.now(UTC) or not user or user.status != "active":
                raise HTTPException(401, "Login session expired or revoked")
        if purpose == "user" and str(user.id) != claims["sub"]:
            raise HTTPException(401, "Invalid authentication credentials")
        return user, login.id, claims["sub"]

    async def rotate(self, refresh: str) -> tuple[Token, str]:
        """Rotate under a row lock; an old refresh token cannot be reused."""
        sid = refresh.split(".")[0]
        async with database_service.session_factory() as db, db.begin():
            row = (await db.exec(select(UserLoginSession).where(UserLoginSession.id == sid).with_for_update())).first()
            if not row or row.revoked_at or row.expires_at <= datetime.now(UTC) or not secrets.compare_digest(row.refresh_token_hash, self.hash_refresh(refresh)):
                raise HTTPException(401, "Invalid refresh token")
            user = await db.get(User, row.user_id)
            if not user or user.status != "active":
                raise HTTPException(401, "Account unavailable")
            value = f"{sid}.{secrets.token_urlsafe(48)}"
            row.refresh_token_hash = self.hash_refresh(value)
            db.add(row)
            token = self.token(str(user.id), "user", sid, user.id)
        return token, value

    async def revoke(self, refresh: str) -> None:
        """Idempotently revoke only the current valid refresh session."""
        async with database_service.session_factory() as db, db.begin():
            row = (await db.exec(select(UserLoginSession).where(UserLoginSession.id == refresh.split(".")[0]).with_for_update())).first()
            if row and secrets.compare_digest(row.refresh_token_hash, self.hash_refresh(refresh)):
                row.revoked_at = datetime.now(UTC)
                db.add(row)


auth_session_service = AuthSessionService()
