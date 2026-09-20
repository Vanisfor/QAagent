"""Real PostgreSQL ownership, ingestion and retrieval path for personal uploads."""

import asyncio
import os
import selectors
from uuid import uuid4
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.main import app
from app.services.database import database_service
from app.services.knowledge import knowledge_service
from app.services.knowledge_access import knowledge_access_service
from app.services.knowledge_ingestion_jobs import knowledge_ingestion_job_service
from app.services.user_knowledge import user_knowledge_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires an isolated migrated PostgreSQL database"
    ),
]


def test_private_upload_is_ingested_and_denied_to_another_user(monkeypatch, tmp_path) -> None:
    """A real queued document becomes searchable only for its authorized owner."""
    assert settings.POSTGRES_DB.startswith("qaagent_user_v1_"), "Use an isolated test database"
    monkeypatch.setattr(settings, "KNOWLEDGE_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)
    app.state.limiter.enabled = False

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for _ in texts:
            vector = [0.0] * settings.EMBEDDING_DIM
            vector[0] = 1.0
            vectors.append(vector)
        return vectors

    monkeypatch.setattr(knowledge_service, "embed", fake_embed)

    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as a:

                async def register() -> tuple[int, dict[str, str]]:
                    email = f"knowledge-{uuid4()}@example.com"
                    response = await a.post(
                        "/api/v1/auth/register",
                        json={"email": email, "username": "Knowledge", "password": "Test-password-1!"},
                    )
                    assert response.status_code == 200, response.text
                    data = response.json()
                    return int(data["id"]), {"Authorization": f"Bearer {data['token']['access_token']}"}

                owner_id, owner_headers = await register()
                other_id, other_headers = await register()
                created = await a.post(
                    "/api/v1/users/me/knowledge-spaces", headers=owner_headers, json={"name": "Private research"}
                )
                assert created.status_code == 201, created.text
                slug = created.json()["slug"]
                route = f"/api/v1/users/me/knowledge-spaces/{slug}/documents"
                assert (await a.get(route, headers=other_headers)).status_code == 404
                uploaded = await a.post(
                    route,
                    headers=owner_headers,
                    files={"file": ("notes.txt", b"alpha private research fact", "text/plain")},
                )
                assert uploaded.status_code == 202, uploaded.text
                job_id = uploaded.json()["job"]["id"]
                claimed = await knowledge_ingestion_job_service._repository.claim(300)
                assert claimed is not None and claimed.id == job_id
                await knowledge_ingestion_job_service._process_with_heartbeat(claimed)
                owner_context = await knowledge_access_service.context_for_user(owner_id, requested_spaces=[slug])
                other_context = await knowledge_access_service.context_for_user(other_id, requested_spaces=[slug])
                assert owner_context.space_slugs == (slug,)
                assert other_context.space_slugs == () and other_context.space_scope_requested
                hits = await knowledge_service.search(
                    "alpha private research fact", context=owner_context, min_similarity=0.0
                )
                assert [hit.source for hit in hits] == ["notes.txt"]
                assert (await a.get(route, headers=owner_headers)).json()[0]["id"] == hits[0].document_id
                deleted = await a.delete(f"{route}/{hits[0].document_id}", headers=owner_headers)
                assert deleted.status_code == 204, deleted.text
                repeated_delete = await a.delete(f"{route}/{hits[0].document_id}", headers=owner_headers)
                assert repeated_delete.status_code == 204, repeated_delete.text
                assert (
                    await knowledge_service.search(
                        "alpha private research fact", context=owner_context, min_similarity=0.0
                    )
                    == []
                )
                reuploaded = await a.post(
                    route,
                    headers=owner_headers,
                    files={"file": ("notes.txt", b"alpha private research fact", "text/plain")},
                )
                assert reuploaded.status_code == 202, reuploaded.text
                second_job_id = reuploaded.json()["job"]["id"]
                assert second_job_id != job_id
                second_claim = await knowledge_ingestion_job_service._repository.claim(300)
                assert second_claim is not None and second_claim.id == second_job_id
                await knowledge_ingestion_job_service._process_with_heartbeat(second_claim)
                second_documents = (await a.get(route, headers=owner_headers)).json()
                assert len(second_documents) == 1
                assert (await a.delete(f"{route}/{second_documents[0]['id']}", headers=owner_headers)).status_code == 204
                failed_upload = await a.post(
                    route, headers=owner_headers,
                    files={"file": ("retry.txt", b"failed then retried", "text/plain")},
                )
                assert failed_upload.status_code == 202
                failed_job_id = failed_upload.json()["job"]["id"]
                failed_claim = await knowledge_ingestion_job_service._repository.claim(300)
                assert failed_claim is not None and failed_claim.id == failed_job_id
                assert await knowledge_ingestion_job_service._repository.fail(
                    failed_claim.id, failed_claim.lease_token, failed_claim.attempts, 1, "test_failure"
                )
                retried = await a.post(
                    route, headers=owner_headers,
                    files={"file": ("retry.txt", b"failed then retried", "text/plain")},
                )
                assert retried.status_code == 202
                retried_job_id = retried.json()["job"]["id"]
                assert retried_job_id != failed_job_id
                retry_claim = await knowledge_ingestion_job_service._repository.claim(300)
                assert retry_claim is not None and retry_claim.id == retried_job_id
                await knowledge_ingestion_job_service._process_with_heartbeat(retry_claim)
                retry_documents = (await a.get(route, headers=owner_headers)).json()
                assert len(retry_documents) == 1
                assert (await a.delete(f"{route}/{retry_documents[0]['id']}", headers=owner_headers)).status_code == 204
                await knowledge_service.grant_space_access(slug, "user", str(other_id), role="editor")
                shared_upload = await a.post(
                    route, headers=other_headers,
                    files={"file": ("shared.txt", b"editor-owned upload", "text/plain")},
                )
                assert shared_upload.status_code == 202, shared_upload.text
                editor_job_id = shared_upload.json()["job"]["id"]
                editor_claim = await knowledge_ingestion_job_service._repository.claim(300)
                assert editor_claim is not None and editor_claim.id == editor_job_id
                await knowledge_ingestion_job_service._process_with_heartbeat(editor_claim)
                editor_job = await user_knowledge_service._jobs.get_for_user(editor_job_id, other_id)
                assert editor_job is not None and Path(editor_job.stored_path).exists()
                editor_document_id = (await a.get(route, headers=owner_headers)).json()[0]["id"]
                assert (await a.delete(f"{route}/{editor_document_id}", headers=other_headers)).status_code == 403
                assert (await a.delete(f"{route}/{editor_document_id}", headers=owner_headers)).status_code == 204
                assert not Path(editor_job.stored_path).exists()
                assert (
                    await a.get(f"/api/v1/users/me/knowledge-ingestion-jobs/{job_id}", headers=other_headers)
                ).status_code == 404
                await user_knowledge_service._jobs.get_for_user(job_id, owner_id)
        finally:
            await knowledge_service.close()
            await database_service.engine.dispose()

    asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
