"""Authentication and authorization endpoints for the API.

This module provides endpoints for user registration, login, session management,
and token verification.
"""

import uuid
from typing import List
from anyio import to_thread
from sqlalchemy.exc import IntegrityError

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import (
    bind_context,
    logger,
)
from app.models.session import Session
from app.models.user import User
from app.schemas.auth import (
    SessionResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.services.database import database_service
from app.services.auth_sessions import auth_session_service
from app.services.user_account import user_account_service
from app.utils.sanitization import (
    sanitize_email,
    sanitize_string,
    validate_password_strength,
)

router = APIRouter()
security = HTTPBearer()
db_service = database_service


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Resolve a current identity from a revocable user access token."""
    user, sid, _ = await auth_session_service.authenticate(credentials.credentials, "user")
    request.state.login_session_id = sid
    bind_context(user_id=user.id)
    return user


async def get_current_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Session:
    """Resolve a conversation and verify both its owner and login session."""
    user, sid, subject = await auth_session_service.authenticate(credentials.credentials, "session")
    session = await db_service.get_session(subject)
    if session is None or session.user_id != user.id:
        raise HTTPException(403, "Cannot access other sessions")
    request.state.login_session_id = sid
    bind_context(user_id=user.id)
    return session


def _set_refresh_cookie(response: Response, value: str) -> None:
    """Use a host-only HttpOnly cookie scoped to authentication endpoints."""
    response.set_cookie("qa_refresh", value, httponly=True, secure=settings.AUTH_COOKIE_SECURE, samesite="strict", path=f"{settings.API_V1_STR}/auth", max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400)


def _require_refresh_header(request: Request) -> None:
    """Require a non-simple header to prevent cross-site form-based refresh/logout."""
    if request.headers.get("x-qa-auth") != "1":
        raise HTTPException(403, "Missing authentication request header")
    origin = request.headers.get("origin")
    trusted = {str(request.base_url).rstrip("/"), *(value.rstrip("/") for value in settings.ALLOWED_ORIGINS if value != "*")}
    if origin and origin.rstrip("/") not in trusted:
        raise HTTPException(403, "Untrusted authentication origin")


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["login"][0])
async def refresh_login(request: Request, response: Response) -> TokenResponse:
    """Rotate the current refresh credential and return a new access token."""
    _require_refresh_header(request)
    token, value = await auth_session_service.rotate(request.cookies.get("qa_refresh", ""))
    _set_refresh_cookie(response, value)
    return TokenResponse(**token.model_dump())


@router.post("/logout", status_code=204)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["login"][0])
async def logout_login(request: Request) -> Response:
    """Revoke this device's login and remove its refresh cookie."""
    _require_refresh_header(request)
    await auth_session_service.revoke(request.cookies.get("qa_refresh", ""))
    response = Response(status_code=204)
    response.delete_cookie("qa_refresh", path=f"{settings.API_V1_STR}/auth", secure=settings.AUTH_COOKIE_SECURE, httponly=True, samesite="strict")
    return response


@router.post("/register", response_model=UserResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["register"][0])
async def register_user(request: Request, response: Response, user_data: UserCreate) -> UserResponse:
    """Register a new user.

    Args:
        request: The FastAPI request object for rate limiting.
        response: Response used to set the refresh cookie.
        user_data: User registration data

    Returns:
        UserResponse: The created user info
    """
    try:
        # Sanitize email
        sanitized_email = sanitize_email(user_data.email)

        # Extract and validate password
        password = user_data.password.get_secret_value()
        validate_password_strength(password)

        # Check if user exists
        if await db_service.get_user_by_email(sanitized_email):
            raise HTTPException(status_code=400, detail="Email already registered")

        # Sanitize optional username
        sanitized_username = sanitize_string(user_data.username) if user_data.username else None

        # Create user
        user = await db_service.create_user(
            email=sanitized_email,
            password=await to_thread.run_sync(User.hash_password, password),
            username=sanitized_username,
        )
        await user_account_service.ensure_defaults(user)

        # Create access token
        token, refresh = await auth_session_service.create(user, request.headers.get("user-agent", ""))
        _set_refresh_cookie(response, refresh)

        return UserResponse(id=user.id, email=user.email, username=user.username, token=token)
    except IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    except ValueError as ve:
        logger.exception("user_registration_validation_failed", error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["login"][0])
async def login(
    request: Request, response: Response, email: str = Form(...), password: str = Form(...), grant_type: str = Form(default="password")
) -> TokenResponse:
    """Login a user.

    Args:
        request: The FastAPI request object for rate limiting.
        response: Response used to set the refresh cookie.
        email: User's email
        password: User's password
        grant_type: Must be "password"

    Returns:
        TokenResponse: Access token information

    Raises:
        HTTPException: If credentials are invalid
    """
    try:
        # Sanitize inputs
        email = sanitize_email(email)
        grant_type = sanitize_string(grant_type)

        # Verify grant type
        if grant_type != "password":
            raise HTTPException(
                status_code=400,
                detail="Unsupported grant type. Must be 'password'",
            )

        user = await db_service.get_user_by_email(email)
        if not user or user.status != "active" or not await to_thread.run_sync(user.verify_password, password):
            raise HTTPException(
                status_code=401,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token, refresh = await auth_session_service.create(user, request.headers.get("user-agent", ""))
        _set_refresh_cookie(response, refresh)
        return TokenResponse(access_token=token.access_token, token_type="bearer", expires_at=token.expires_at)
    except ValueError as ve:
        logger.exception("login_validation_failed", error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))


@router.post("/session", response_model=SessionResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["session"][0])
async def create_session(request: Request, user: User = Depends(get_current_user)):
    """Create a new chat session for the authenticated user.

    Args:
        request: FastAPI request used by the rate limiter.
        user: The authenticated user

    Returns:
        SessionResponse: The session ID, name, and access token
    """
    try:
        # Generate a unique session ID
        session_id = str(uuid.uuid4())

        # Create session in database, copying username for LLM personalization
        session = await db_service.create_session(session_id, user.id, username=user.username)

        # Create access token for the session
        token = auth_session_service.token(session_id, "session", request.state.login_session_id, user.id)

        logger.info(
            "session_created",
            session_id=session_id,
            user_id=user.id,
            name=session.name,
            expires_at=token.expires_at.isoformat(),
        )

        return SessionResponse(session_id=session_id, name=session.name, token=token)
    except ValueError as ve:
        logger.exception("session_creation_validation_failed", error=str(ve), user_id=user.id)
        raise HTTPException(status_code=422, detail=str(ve))


@router.patch("/session/{session_id}/name", response_model=SessionResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["session"][0])
async def update_session_name(
    request: Request, session_id: str, name: str = Form(...), current_session: Session = Depends(get_current_session)
):
    """Update a session's name.

    Args:
        request: FastAPI request used by the rate limiter.
        session_id: The ID of the session to update
        name: The new name for the session
        current_session: The current session from auth

    Returns:
        SessionResponse: The updated session information
    """
    try:
        # Sanitize inputs
        sanitized_session_id = sanitize_string(session_id)
        sanitized_name = sanitize_string(name)
        sanitized_current_session = sanitize_string(current_session.id)

        # Verify the session ID matches the authenticated session
        if sanitized_session_id != sanitized_current_session:
            raise HTTPException(status_code=403, detail="Cannot modify other sessions")

        # Update the session name
        session = await db_service.update_session_name(sanitized_session_id, sanitized_name)

        # Create a new token (not strictly necessary but maintains consistency)
        token = auth_session_service.token(sanitized_session_id, "session", request.state.login_session_id, current_session.user_id)

        return SessionResponse(session_id=sanitized_session_id, name=session.name, token=token)
    except ValueError as ve:
        logger.exception("session_update_validation_failed", error=str(ve), session_id=session_id)
        raise HTTPException(status_code=422, detail=str(ve))


@router.delete("/session/{session_id}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["session"][0])
async def delete_session(request: Request, session_id: str, current_session: Session = Depends(get_current_session)):
    """Delete a session for the authenticated user.

    Args:
        request: FastAPI request used by the rate limiter.
        session_id: The ID of the session to delete
        current_session: The current session from auth

    Returns:
        None
    """
    try:
        # Sanitize inputs
        sanitized_session_id = sanitize_string(session_id)
        sanitized_current_session = sanitize_string(current_session.id)

        # Verify the session ID matches the authenticated session
        if sanitized_session_id != sanitized_current_session:
            raise HTTPException(status_code=403, detail="Cannot delete other sessions")

        # Delete the session
        await db_service.delete_session(sanitized_session_id)

        logger.info("session_deleted", session_id=session_id, user_id=current_session.user_id)
    except ValueError as ve:
        logger.exception("session_deletion_validation_failed", error=str(ve), session_id=session_id)
        raise HTTPException(status_code=422, detail=str(ve))


@router.get("/sessions", response_model=List[SessionResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def get_user_sessions(request: Request, user: User = Depends(get_current_user)):
    """Get all session IDs for the authenticated user.

    Args:
        request: FastAPI request used by the rate limiter.
        user: The authenticated user

    Returns:
        List[SessionResponse]: List of session IDs
    """
    try:
        sessions = await db_service.get_user_sessions(user.id)
        return [
            SessionResponse(
                session_id=sanitize_string(session.id),
                name=sanitize_string(session.name),
                token=auth_session_service.token(session.id, "session", request.state.login_session_id, user.id),
            )
            for session in sessions
        ]
    except ValueError as ve:
        logger.exception("get_sessions_validation_failed", user_id=user.id, error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))
