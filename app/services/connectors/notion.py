"""Notion connector using paginated page search and block traversal."""

import hashlib
from datetime import datetime
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.connectors.base import ConnectorDocument, ConnectorSyncBatch, ExternalPrincipalRef


class NotionConnector:
    """Synchronize pages shared with a Notion connection."""

    def __init__(
        self,
        *,
        token: str,
        workspace_external_id: str,
        api_url: str = "https://api.notion.com/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure a connection-scoped Notion workspace reader."""
        self._token = token
        self._workspace_external_id = workspace_external_id
        self._api_url = api_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{self._api_url}/",
                headers={"Authorization": f"Bearer {self._token}", "Notion-Version": "2026-03-11"},
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
        raise RuntimeError("Notion request exhausted without a response")

    @staticmethod
    def _plain_text(block: dict[str, Any]) -> str:
        block_type = str(block.get("type", ""))
        payload = block.get(block_type, {}) if block_type else {}
        rich_text = payload.get("rich_text") or payload.get("text") or []
        return "".join(str(item.get("plain_text", "")) for item in rich_text)

    async def _page_content(self, page_id: str) -> str:
        lines: list[str] = []
        pending: list[str] = [page_id]
        while pending:
            block_id = pending.pop(0)
            cursor: str | None = None
            while True:
                params: dict[str, Any] = {"page_size": 100}
                if cursor:
                    params["start_cursor"] = cursor
                body = (await self._request("GET", f"blocks/{block_id}/children", params=params)).json()
                for block in body.get("results", []):
                    text_value = self._plain_text(block)
                    if text_value:
                        lines.append(text_value)
                    if block.get("has_children"):
                        pending.append(str(block["id"]))
                if not body.get("has_more"):
                    break
                cursor = body.get("next_cursor")
        return "\n".join(lines)

    @staticmethod
    def _title(page: dict[str, Any]) -> str:
        for prop in page.get("properties", {}).values():
            title_items = prop.get("title") if isinstance(prop, dict) else None
            if title_items:
                return "".join(str(item.get("plain_text", "")) for item in title_items) or "Untitled"
        return "Untitled"

    async def fetch_changes(self, cursor: dict[str, Any] | None) -> ConnectorSyncBatch:
        """Enumerate shared pages, read changed blocks and emit archived tombstones."""
        previous = dict((cursor or {}).get("pages", {}))
        pages: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            payload: dict[str, Any] = {
                "page_size": 100,
                "filter": {"property": "object", "value": "page"},
                "sort": {"direction": "ascending", "timestamp": "last_edited_time"},
            }
            if start_cursor:
                payload["start_cursor"] = start_cursor
            body = (await self._request("POST", "search", json=payload)).json()
            pages.extend(body.get("results", []))
            if not body.get("has_more"):
                break
            start_cursor = body.get("next_cursor")

        current: dict[str, str] = {}
        documents: list[ConnectorDocument] = []
        deleted: set[str] = set()
        workspace_acl = (
            ExternalPrincipalRef("group", f"notion:{self._workspace_external_id}", "Notion workspace members"),
        )
        for page in pages:
            page_id = str(page["id"])
            edited = str(page.get("last_edited_time", ""))
            if page.get("archived") or page.get("in_trash"):
                deleted.add(page_id)
                continue
            current[page_id] = edited
            if previous.get(page_id) == edited:
                continue
            content = await self._page_content(page_id)
            documents.append(
                ConnectorDocument(
                    external_id=page_id,
                    source=str(page.get("url") or f"notion:{page_id}"),
                    title=self._title(page),
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    metadata={"notion_page_id": page_id, "url": page.get("url")},
                    source_updated_at=datetime.fromisoformat(edited.replace("Z", "+00:00")) if edited else None,
                    acl_principals=workspace_acl,
                )
            )
        deleted.update(set(previous) - set(current))
        return ConnectorSyncBatch(
            documents=tuple(documents),
            deleted_external_ids=tuple(sorted(deleted)),
            next_cursor={"version": 1, "pages": current},
        )

    async def close(self) -> None:
        """Close the internally owned HTTP client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
