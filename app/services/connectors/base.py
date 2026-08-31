"""Provider-neutral contracts for incremental enterprise data synchronization."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class ExternalPrincipalRef:
    """Provider identity attached to a source ACL snapshot."""

    principal_type: str
    external_id: str
    display_name: str = ""


@dataclass(frozen=True)
class ExternalGroupMembership:
    """Provider group and the provider user IDs observed as its members."""

    external_group_id: str
    display_name: str
    user_external_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConnectorDocument:
    """One normalized source document emitted by a connector."""

    external_id: str
    source: str
    title: str
    content: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source_updated_at: datetime | None = None
    acl_principals: tuple[ExternalPrincipalRef, ...] = ()


@dataclass(frozen=True)
class ConnectorSyncBatch:
    """One finite batch of changes and its durable next cursor."""

    documents: tuple[ConnectorDocument, ...]
    deleted_external_ids: tuple[str, ...]
    next_cursor: dict[str, Any]
    has_more: bool = False
    group_memberships: tuple[ExternalGroupMembership, ...] = ()


class KnowledgeConnector(Protocol):
    """Incremental connector interface implemented by every enterprise source."""

    async def fetch_changes(self, cursor: dict[str, Any] | None) -> ConnectorSyncBatch:
        """Return changes after the supplied durable cursor."""
        ...
