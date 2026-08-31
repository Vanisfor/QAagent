"""Provider-contract tests for GitHub, Notion and SharePoint connectors."""

import asyncio

import httpx

from app.services.connectors.github import GitHubConnector
from app.services.connectors.notion import NotionConnector
from app.services.connectors.sharepoint import SharePointConnector


def test_github_connector_uses_tree_content_and_collaborator_apis() -> None:
    """GitHub changes carry repository collaborator principals into document ACLs."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if "/git/trees/main" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "sha": "tree-2",
                    "truncated": False,
                    "tree": [{"path": "docs/a.md", "type": "blob", "sha": "2"}],
                },
            )
        if request.url.path.endswith("/collaborators"):
            return httpx.Response(200, json=[{"login": "octocat"}])
        if "/contents/docs/a.md" in request.url.path:
            return httpx.Response(200, text="GitHub policy")
        raise AssertionError(request.url)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.test/")
    connector = GitHubConnector(
        token="token",
        owner="acme",
        repository="handbook",
        api_url="https://api.github.test",
        client=client,
    )
    try:
        batch = asyncio.run(connector.fetch_changes({"files": {"old.md": "1"}}))
    finally:
        asyncio.run(client.aclose())

    assert [document.external_id for document in batch.documents] == ["docs/a.md"]
    assert batch.documents[0].acl_principals[0].external_id == "octocat"
    assert batch.deleted_external_ids == ("old.md",)


def test_notion_connector_paginates_pages_and_reads_block_children() -> None:
    """Notion pages shared with the connection become workspace-ACL documents."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "has_more": False,
                    "next_cursor": None,
                    "results": [
                        {
                            "id": "page-1",
                            "object": "page",
                            "last_edited_time": "2026-08-30T10:00:00Z",
                            "url": "https://notion.test/page-1",
                            "properties": {
                                "Name": {"title": [{"plain_text": "Runbook"}]},
                            },
                        }
                    ],
                },
            )
        if "/blocks/page-1/children" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "has_more": False,
                    "next_cursor": None,
                    "results": [
                        {
                            "id": "block-1",
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {"rich_text": [{"plain_text": "Notion policy"}]},
                        }
                    ],
                },
            )
        raise AssertionError(request.url)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.notion.test/v1/")
    connector = NotionConnector(
        token="token",
        workspace_external_id="workspace-1",
        api_url="https://api.notion.test/v1",
        client=client,
    )
    try:
        batch = asyncio.run(connector.fetch_changes(None))
    finally:
        asyncio.run(client.aclose())

    assert batch.documents[0].title == "Runbook"
    assert batch.documents[0].content == "Notion policy"
    assert batch.documents[0].acl_principals[0].principal_type == "group"


def test_sharepoint_connector_consumes_delta_content_permissions_and_deletes() -> None:
    """SharePoint delta items include explicit Graph permission snapshots and tombstones."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/root/delta"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "item-1",
                            "name": "policy.md",
                            "file": {},
                            "webUrl": "https://sharepoint.test/policy",
                            "lastModifiedDateTime": "2026-08-30T10:00:00Z",
                        },
                        {"id": "deleted-1", "deleted": {}},
                    ],
                    "@odata.deltaLink": "https://graph.test/v1.0/drives/drive-1/root/delta?token=next",
                },
            )
        if request.url.path.endswith("/item-1/content"):
            return httpx.Response(200, text="SharePoint policy")
        if request.url.path.endswith("/item-1/permissions"):
            return httpx.Response(
                200,
                json={
                    "value": [{"grantedToV2": {"sharePointGroup": {"id": "group-1", "displayName": "Engineering"}}}]
                },
            )
        raise AssertionError(request.url)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://graph.test/v1.0/")
    connector = SharePointConnector(
        token="token",
        drive_id="drive-1",
        api_url="https://graph.test/v1.0",
        client=client,
    )
    try:
        batch = asyncio.run(connector.fetch_changes(None))
    finally:
        asyncio.run(client.aclose())

    assert batch.documents[0].content == "SharePoint policy"
    assert batch.documents[0].acl_principals[0].external_id == "group-1"
    assert batch.deleted_external_ids == ("deleted-1",)
    assert "token=next" in batch.next_cursor["delta_link"]
