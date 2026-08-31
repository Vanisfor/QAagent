"""SharePoint/OneDrive connector using Microsoft Graph delta and permissions APIs."""

import hashlib
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.connectors.base import ConnectorDocument, ConnectorSyncBatch, ExternalPrincipalRef

_SUPPORTED_EXTENSIONS = {".md", ".txt", ".rst"}


class SharePointConnector:
    """Synchronize drive changes, content and item permission principals."""

    def __init__(
        self,
        *,
        token: str,
        drive_id: str,
        api_url: str = "https://graph.microsoft.com/v1.0",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure one Microsoft Graph drive delta feed."""
        self._token = token
        self._drive_id = drive_id
        self._api_url = api_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{self._api_url}/",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=30,
            )
        return self._client

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        client = self._get_client()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
        raise RuntimeError("Microsoft Graph request exhausted without a response")

    @staticmethod
    def _permission_principals(body: dict[str, Any]) -> tuple[ExternalPrincipalRef, ...]:
        principals: dict[tuple[str, str], ExternalPrincipalRef] = {}
        for permission in body.get("value", []):
            identities = permission.get("grantedToIdentitiesV2") or [permission.get("grantedToV2", {})]
            for identity_set in identities:
                if not isinstance(identity_set, dict):
                    continue
                for key, value in identity_set.items():
                    if not isinstance(value, dict) or not value.get("id"):
                        continue
                    principal_type = "group" if "group" in key.lower() else "user"
                    external_id = str(value["id"])
                    principals[(principal_type, external_id)] = ExternalPrincipalRef(
                        principal_type,
                        external_id,
                        str(value.get("displayName") or external_id),
                    )
        return tuple(principals.values())

    async def fetch_changes(self, cursor: dict[str, Any] | None) -> ConnectorSyncBatch:
        """Consume the full delta page chain and emit content plus permission snapshots."""
        delta_url = str((cursor or {}).get("delta_link") or f"drives/{self._drive_id}/root/delta")
        items: dict[str, dict[str, Any]] = {}
        final_delta_link = delta_url
        while True:
            response = await self._request(
                "GET",
                delta_url,
                headers={"Prefer": "deltashowremovedasdeleted, deltatraversepermissiongaps, deltashowsharingchanges"},
            )
            body = response.json()
            for item in body.get("value", []):
                items[str(item["id"])] = item
            next_link = body.get("@odata.nextLink")
            final_delta_link = str(body.get("@odata.deltaLink") or final_delta_link)
            if not next_link:
                break
            delta_url = str(next_link)

        documents: list[ConnectorDocument] = []
        deleted: list[str] = []
        for item_id, item in items.items():
            if "deleted" in item:
                deleted.append(item_id)
                continue
            name = str(item.get("name", ""))
            if "file" not in item or PurePosixPath(name).suffix.lower() not in _SUPPORTED_EXTENSIONS:
                continue
            content_response = await self._request("GET", f"drives/{self._drive_id}/items/{item_id}/content")
            permissions_response = await self._request("GET", f"drives/{self._drive_id}/items/{item_id}/permissions")
            content = content_response.text
            modified = item.get("lastModifiedDateTime")
            documents.append(
                ConnectorDocument(
                    external_id=item_id,
                    source=str(item.get("webUrl") or f"sharepoint:{item_id}"),
                    title=name,
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    metadata={"drive_id": self._drive_id, "item_id": item_id, "web_url": item.get("webUrl")},
                    source_updated_at=(
                        datetime.fromisoformat(str(modified).replace("Z", "+00:00")) if modified else None
                    ),
                    acl_principals=self._permission_principals(permissions_response.json()),
                )
            )
        return ConnectorSyncBatch(
            documents=tuple(documents),
            deleted_external_ids=tuple(sorted(deleted)),
            next_cursor={"version": 1, "delta_link": final_delta_link},
        )

    async def close(self) -> None:
        """Close the internally owned HTTP client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
