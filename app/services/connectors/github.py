"""GitHub repository connector using versioned REST tree and contents APIs."""

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.connectors.base import ConnectorDocument, ConnectorSyncBatch, ExternalPrincipalRef

_SUPPORTED_EXTENSIONS = {".md", ".txt", ".rst"}


class GitHubConnector:
    """Synchronize text documents and collaborator ACLs from one repository."""

    def __init__(
        self,
        *,
        token: str,
        owner: str,
        repository: str,
        branch: str = "main",
        api_url: str = "https://api.github.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure one repository and a lazy versioned GitHub client."""
        self._token = token
        self._owner = owner
        self._repository = repository
        self._branch = branch
        self._api_url = api_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{self._api_url}/",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
                timeout=20,
            )
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._get_client()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response
        raise RuntimeError("GitHub request exhausted without a response")

    async def _collaborators(self) -> tuple[ExternalPrincipalRef, ...]:
        response = await self._request(
            "GET",
            f"repos/{self._owner}/{self._repository}/collaborators",
            params={"affiliation": "all", "per_page": 100},
        )
        return tuple(
            ExternalPrincipalRef("user", str(item["login"]), str(item.get("name") or item["login"]))
            for item in response.json()
        )

    async def fetch_changes(self, cursor: dict[str, Any] | None) -> ConnectorSyncBatch:
        """Read a recursive tree, changed raw files and repository collaborators."""
        previous = dict((cursor or {}).get("files", {}))
        tree_response, principals = await asyncio.gather(
            self._request(
                "GET",
                f"repos/{self._owner}/{self._repository}/git/trees/{quote(self._branch, safe='')}",
                params={"recursive": "1"},
            ),
            self._collaborators(),
        )
        tree_body = tree_response.json()
        if tree_body.get("truncated"):
            raise RuntimeError("GitHub recursive tree was truncated")
        blobs = [
            item
            for item in tree_body.get("tree", [])
            if item.get("type") == "blob"
            and PurePosixPath(str(item.get("path", ""))).suffix.lower() in _SUPPORTED_EXTENSIONS
        ]
        current = {str(item["path"]): str(item["sha"]) for item in blobs}
        changed = [item for item in blobs if previous.get(str(item["path"])) != str(item["sha"])]

        async def load(item: dict[str, Any]) -> ConnectorDocument:
            path = str(item["path"])
            response = await self._request(
                "GET",
                f"repos/{self._owner}/{self._repository}/contents/{quote(path, safe='/')}",
                params={"ref": self._branch},
                headers={"Accept": "application/vnd.github.raw+json"},
            )
            content = response.text
            return ConnectorDocument(
                external_id=path,
                source=f"github:{self._owner}/{self._repository}/{path}",
                title=PurePosixPath(path).name,
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                metadata={"repository": f"{self._owner}/{self._repository}", "path": path, "branch": self._branch},
                source_updated_at=datetime.now(UTC),
                acl_principals=principals,
            )

        documents = tuple(await asyncio.gather(*(load(item) for item in changed)))
        return ConnectorSyncBatch(
            documents=documents,
            deleted_external_ids=tuple(sorted(set(previous) - set(current))),
            next_cursor={"version": 1, "files": current, "tree_sha": tree_body.get("sha")},
        )

    async def close(self) -> None:
        """Close the internally owned HTTP client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
