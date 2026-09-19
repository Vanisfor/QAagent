"""Real PostgreSQL authentication, ownership and durable account API tests."""

import asyncio
import io
import os
import selectors
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from app.core.config import settings
from app.main import app
from app.services.auth_sessions import auth_session_service
from app.services.database import database_service
from app.services.user_account import user_account_service
from app.utils.auth import create_access_token, decode_access_token
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from alembic import command
from alembic.config import Config

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.getenv("RUN_USER_SYSTEM_TESTS") != "1", reason="requires isolated migrated PostgreSQL database")]


def test_user_account_end_to_end_in_postgres(monkeypatch, tmp_path) -> None:
    """Exercise real registration, refresh, logout, profile and cross-user guards."""
    assert settings.POSTGRES_DB.startswith("qaagent_user_v1_"), "Use an isolated user-system test database"
    monkeypatch.setattr(settings, "USER_MEDIA_DIR", tmp_path)
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)
    app.state.limiter.enabled = False

    async def run() -> None:
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as a, httpx.AsyncClient(transport=transport, base_url="http://test") as b:
                email_a = f"a-{uuid4()}@example.com"
                email_b = f"b-{uuid4()}@example.com"
                async def register(client, email):
                    response = await client.post("/api/v1/auth/register", json={"email": email, "password": "Test-password-1!", "username": "Demo"})
                    assert response.status_code == 200, response.text
                    cookie = response.headers["set-cookie"]
                    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
                    data = response.json()
                    assert "hashed_password" not in response.text
                    return data["id"], data["token"]["access_token"]
                uid_a, token_a = await register(a, email_a)
                _, token_b = await register(b, email_b)
                headers_a = {"Authorization": f"Bearer {token_a}"}
                headers_b = {"Authorization": f"Bearer {token_b}"}
                assert (await a.post("/api/v1/auth/register", json={"email": email_a, "password": "Test-password-1!"})).status_code == 400
                identity = await a.get("/api/v1/users/me", headers=headers_a)
                assert identity.json()["email"] == email_a
                assert "hashed_password" not in identity.text
                profile = {"display_name": "Alice", "bio": "Private profile", "language": "auto", "timezone": "Asia/Shanghai"}
                assert (await a.patch("/api/v1/users/me/profile", headers=headers_a, json=profile)).status_code == 200
                assert (await a.patch("/api/v1/users/me/profile", headers=headers_a, json={"bio": "Updated bio"})).json()["display_name"] == "Alice"
                assert (await a.patch("/api/v1/users/me/profile", headers=headers_a, json={"timezone": "invalid-zone"})).status_code == 422
                assert (await b.get("/api/v1/users/me/profile", headers=headers_b)).json()["display_name"] == "Demo"
                assert (await a.patch("/api/v1/users/me/profile", headers=headers_a, json={**profile, "user_id": 999})).status_code == 422
                appearance = {"theme": "dark", "sidebar_collapsed": True, "background": {"preset": "ocean"}}
                assert (await a.patch("/api/v1/users/me/settings", headers=headers_a, json=appearance)).status_code == 200
                assert (await a.get("/api/v1/users/me/settings", headers=headers_a)).json()["theme"] == "dark"
                assert (await a.patch("/api/v1/users/me/settings", headers=headers_a, json={"sidebar_collapsed": False})).json()["theme"] == "dark"
                assert (await b.get("/api/v1/users/me/settings", headers=headers_b)).json()["theme"] == "light"
                assert (await a.patch("/api/v1/users/me/personalization", headers=headers_a, json={"custom_instructions": "Use examples", "memory_enabled": False})).status_code == 200
                assert (await user_account_service.personalization(uid_a)).memory_enabled is False
                assert (await b.get("/api/v1/users/me/personalization", headers=headers_b)).json()["memory_enabled"] is True
                source = io.BytesIO()
                Image.new("RGB", (32, 32), "red").save(source, format="PNG")
                uploaded = await a.post("/api/v1/users/me/avatar", headers=headers_a, files={"file": ("avatar.png", source.getvalue(), "image/png")})
                assert uploaded.status_code == 200, uploaded.text
                url = uploaded.json()["avatar_url"]
                assert (await a.get(url, headers=headers_a)).status_code == 200
                assert (await b.get(url, headers=headers_b)).status_code == 404
                assert (await a.get(url)).status_code in (401, 403)
                assert (await a.post("/api/v1/users/me/avatar", headers=headers_a, files={"file": ("a.png", b"invalid", "image/png")})).status_code == 422
                assert (await a.delete("/api/v1/users/me/avatar", headers=headers_a)).status_code == 204
                assert not list(tmp_path.iterdir())
                conversation = (await a.post("/api/v1/auth/session", headers=headers_a)).json()
                cid = conversation["session_id"]
                chat_token = conversation["token"]["access_token"]
                chat_headers = {"Authorization": f"Bearer {chat_token}"}
                sessions_b = (await b.get("/api/v1/auth/sessions", headers=headers_b)).json()
                assert cid not in [row["session_id"] for row in sessions_b]
                claims_b = decode_access_token(token_b, "user")
                forged_owner = auth_session_service.token(cid, "session", claims_b["sid"], int(claims_b["uid"]))
                assert (await b.get("/api/v1/chatbot/messages", headers={"Authorization": f"Bearer {forged_owner.access_token}"})).status_code == 403
                assert (await b.delete(f"/api/v1/auth/session/{cid}", headers=headers_b)).status_code == 401
                chat_b = (await b.post("/api/v1/auth/session", headers=headers_b)).json()["token"]["access_token"]
                for method, path in (("DELETE", f"/api/v1/auth/session/{cid}"), ("PATCH", f"/api/v1/auth/session/{cid}/name")):
                    assert (await b.request(method, path, headers={"Authorization": f"Bearer {chat_b}"}, data={"name": "forbidden"})).status_code == 403
                assert (await a.patch(f"/api/v1/auth/session/{cid}/name", headers=chat_headers, data={"name": "Alice chat"})).status_code == 200
                original_cookie = a.cookies.get("qa_refresh")
                refreshed = await a.post("/api/v1/auth/refresh", headers={"X-QA-Auth": "1"})
                assert refreshed.status_code == 200
                assert original_cookie != a.cookies.get("qa_refresh")
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as replay:
                    replay.cookies.set("qa_refresh", original_cookie)
                    assert (await replay.post("/api/v1/auth/refresh", headers={"X-QA-Auth": "1"})).status_code == 401
                assert (await a.post("/api/v1/auth/logout")).status_code == 403
                assert (await a.post("/api/v1/auth/refresh", headers={"X-QA-Auth": "1", "Origin": "https://malicious.example"})).status_code == 403
                assert (await a.post("/api/v1/auth/logout", headers={"X-QA-Auth": "1"})).status_code == 204
                assert (await a.get("/api/v1/users/me", headers=headers_a)).status_code == 401
                assert (await a.get("/api/v1/chatbot/messages", headers=chat_headers)).status_code == 401
                assert (await a.post("/api/v1/auth/refresh", headers={"X-QA-Auth": "1"})).status_code == 401
                assert (await b.get("/api/v1/users/me", headers=headers_b)).status_code == 200
                login = await a.post("/api/v1/auth/login", data={"email": email_a.upper(), "password": "Test-password-1!"})
                assert login.status_code == 200
                legacy = create_access_token(str(uid_a), "user", timedelta(minutes=5))
                assert (await a.get("/api/v1/users/me", headers={"Authorization": f"Bearer {legacy.access_token}"})).status_code == 401
                new_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                assert (await a.get("/api/v1/users/me", headers=new_headers)).json()["last_login_at"]
        finally:
            app.state.limiter.enabled = True
            await database_service.engine.dispose()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


def test_migration_retains_existing_user_and_conversation() -> None:
    """Upgrade from the prior revision preserves identity, password hash and chat FK."""
    assert settings.POSTGRES_DB.startswith("qaagent_user_v1_"), "Use an isolated test database"
    config = Config("alembic.ini")
    command.downgrade(config, "f39a2c7d8e10")
    engine = create_engine(URL.create("postgresql+psycopg", username=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD, host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB))
    email = f"legacy-{uuid4()}@example.com"
    cid = str(uuid4())
    try:
        with engine.begin() as db:
            uid = db.execute(text('INSERT INTO "user" (email, hashed_password, username, created_at) VALUES (:email, :hash, \'Legacy User\', now()) RETURNING id'), {"email": email, "hash": "existing-hash"}).scalar_one()
            db.execute(text('INSERT INTO session (id, user_id, name, created_at) VALUES (:cid, :uid, \'Legacy Chat\', now())'), {"cid": cid, "uid": uid})
        command.upgrade(config, "head")
        with engine.connect() as db:
            row = db.execute(text('SELECT u.id, u.hashed_password, p.display_name, u.status, s.user_id FROM "user" u JOIN user_profiles p ON p.user_id=u.id JOIN session s ON s.user_id=u.id WHERE u.id=:uid AND s.id=:cid'), {"uid": uid, "cid": cid}).one()
            assert tuple(row) == (uid, "existing-hash", "Legacy User", "active", uid)
    finally:
        command.upgrade(config, "head")
        engine.dispose()
