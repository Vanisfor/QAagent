"""Map external connector identities and synchronize document ACL snapshots."""

from typing import Any

from sqlalchemy import text

from app.services.connectors.base import ExternalGroupMembership, ExternalPrincipalRef
from app.services.database import database_service


class ExternalACLService:
    """Synchronize provider identities into organization-scoped user/group grants."""

    async def _connector_organization(self, connector_id: int) -> int:
        query: Any = text(
            """
            SELECT space.organization_id
            FROM knowledge_connectors AS connector
            JOIN knowledge_spaces AS space ON space.id = connector.space_id
            WHERE connector.id = :connector_id
            """
        )
        async with database_service.session_factory() as session:
            value = (await session.exec(query, params={"connector_id": connector_id})).scalar_one_or_none()
        if value is None:
            raise ValueError(f"knowledge connector not found: {connector_id}")
        return int(value)

    async def sync_group_memberships(
        self,
        connector_id: int,
        memberships: tuple[ExternalGroupMembership, ...],
    ) -> None:
        """Replace observed provider group memberships for locally mapped users."""
        if not memberships:
            return
        organization_id = await self._connector_organization(connector_id)
        delete_members: Any = text("DELETE FROM knowledge_group_members WHERE group_id = :group_id")
        insert_member: Any = text(
            """
            INSERT INTO knowledge_group_members (group_id, user_id)
            VALUES (:group_id, :user_id) ON CONFLICT DO NOTHING
            """
        )
        async with database_service.session_factory() as session, session.begin():
            for membership in memberships:
                group_id = await self._upsert_group(
                    session,
                    connector_id,
                    organization_id,
                    membership.external_group_id,
                    membership.display_name,
                )
                await session.exec(delete_members, params={"group_id": group_id})
                for external_user_id in membership.user_external_ids:
                    user_id = await self._mapped_user_id(
                        session,
                        connector_id,
                        organization_id,
                        external_user_id,
                    )
                    if user_id is not None:
                        await session.exec(insert_member, params={"group_id": group_id, "user_id": user_id})

    async def map_external_user(self, connector_id: int, external_id: str, local_user_id: int) -> None:
        """Explicitly bind a provider user ID such as a GitHub login to a local user."""
        organization_id = await self._connector_organization(connector_id)
        statement: Any = text(
            """
            INSERT INTO connector_external_principals (
                connector_id, principal_type, external_id, mapped_user_id
            )
            SELECT :connector_id, 'user', :external_id, member.user_id
            FROM organization_members AS member
            WHERE member.organization_id = :organization_id AND member.user_id = :local_user_id
            ON CONFLICT (connector_id, principal_type, external_id) DO UPDATE
            SET mapped_user_id = EXCLUDED.mapped_user_id, updated_at = now()
            RETURNING mapped_user_id
            """
        )
        async with database_service.session_factory() as session, session.begin():
            value = (
                await session.exec(
                    statement,
                    params={
                        "connector_id": connector_id,
                        "external_id": external_id,
                        "organization_id": organization_id,
                        "local_user_id": local_user_id,
                    },
                )
            ).scalar_one_or_none()
        if value is None:
            raise ValueError("local user is not a member of the connector organization")

    async def add_external_group_member(
        self,
        connector_id: int,
        external_group_id: str,
        local_user_id: int,
    ) -> None:
        """Add a local organization member to a provider group mapping."""
        organization_id = await self._connector_organization(connector_id)
        statement: Any = text(
            """
            INSERT INTO knowledge_group_members (group_id, user_id)
            SELECT knowledge_group.id, member.user_id
            FROM knowledge_groups AS knowledge_group
            JOIN organization_members AS member
              ON member.organization_id = knowledge_group.organization_id
             AND member.user_id = :local_user_id
            WHERE knowledge_group.organization_id = :organization_id
              AND knowledge_group.connector_id = :connector_id
              AND knowledge_group.external_id = :external_group_id
            ON CONFLICT DO NOTHING
            RETURNING user_id
            """
        )
        async with database_service.session_factory() as session, session.begin():
            value = (
                await session.exec(
                    statement,
                    params={
                        "connector_id": connector_id,
                        "organization_id": organization_id,
                        "external_group_id": external_group_id,
                        "local_user_id": local_user_id,
                    },
                )
            ).scalar_one_or_none()
        if value is None:
            raise ValueError("external group or local organization member was not found")

    async def apply_document_acl(
        self,
        connector_id: int,
        *,
        space_slug: str,
        source_type: str,
        external_id: str,
        principals: tuple[ExternalPrincipalRef, ...],
    ) -> int:
        """Replace one connector-managed document ACL snapshot with mapped principals."""
        organization_id = await self._connector_organization(connector_id)
        document_query: Any = text(
            """
            SELECT document.id
            FROM knowledge_documents AS document
            JOIN knowledge_spaces AS space ON space.id = document.space_id
            WHERE space.slug = :space_slug
              AND space.organization_id = :organization_id
              AND document.source_type = :source_type
              AND document.external_id = :external_id
            """
        )
        delete_acl: Any = text(
            """
            DELETE FROM knowledge_document_principals
            WHERE document_id = :document_id AND managed_by_connector_id = :connector_id
            """
        )
        insert_acl: Any = text(
            """
            INSERT INTO knowledge_document_principals (
                document_id, principal_type, principal_id, managed_by_connector_id
            ) VALUES (:document_id, :principal_type, :principal_id, :connector_id)
            ON CONFLICT (document_id, principal_type, principal_id) DO UPDATE
            SET managed_by_connector_id = EXCLUDED.managed_by_connector_id
            """
        )
        async with database_service.session_factory() as session, session.begin():
            document_id = (
                await session.exec(
                    document_query,
                    params={
                        "space_slug": space_slug,
                        "organization_id": organization_id,
                        "source_type": source_type,
                        "external_id": external_id,
                    },
                )
            ).scalar_one_or_none()
            if document_id is None:
                raise ValueError("synchronized document not found for ACL update")
            await session.exec(delete_acl, params={"document_id": int(document_id), "connector_id": connector_id})
            inserted = 0
            for principal in principals:
                principal_type, principal_id = await self._map_principal(
                    session,
                    connector_id,
                    organization_id,
                    principal,
                )
                if principal_id is None:
                    continue
                await session.exec(
                    insert_acl,
                    params={
                        "document_id": int(document_id),
                        "principal_type": principal_type,
                        "principal_id": principal_id,
                        "connector_id": connector_id,
                    },
                )
                inserted += 1
        return inserted

    async def _map_principal(
        self,
        session: Any,
        connector_id: int,
        organization_id: int,
        principal: ExternalPrincipalRef,
    ) -> tuple[str, str | None]:
        if principal.principal_type == "group":
            group_id = await self._upsert_group(
                session,
                connector_id,
                organization_id,
                principal.external_id,
                principal.display_name or principal.external_id,
            )
            return "group", str(group_id)
        if principal.principal_type != "user":
            raise ValueError(f"unsupported external principal type: {principal.principal_type}")
        user_id = await self._mapped_user_id(session, connector_id, organization_id, principal.external_id)
        await session.exec(
            text(
                """
                INSERT INTO connector_external_principals (
                    connector_id, principal_type, external_id, display_name, mapped_user_id
                ) VALUES (:connector_id, 'user', :external_id, :display_name, :user_id)
                ON CONFLICT (connector_id, principal_type, external_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    mapped_user_id = COALESCE(EXCLUDED.mapped_user_id, connector_external_principals.mapped_user_id),
                    updated_at = now()
                """
            ),
            params={
                "connector_id": connector_id,
                "external_id": principal.external_id,
                "display_name": principal.display_name,
                "user_id": user_id,
            },
        )
        return "user", str(user_id) if user_id is not None else None

    async def _upsert_group(
        self,
        session: Any,
        connector_id: int,
        organization_id: int,
        external_id: str,
        display_name: str,
    ) -> int:
        group_result = await session.exec(
            text(
                """
                INSERT INTO knowledge_groups (
                    organization_id, connector_id, external_id, display_name
                ) VALUES (:organization_id, :connector_id, :external_id, :display_name)
                ON CONFLICT (organization_id, connector_id, external_id) DO UPDATE
                SET display_name = EXCLUDED.display_name, updated_at = now()
                RETURNING id
                """
            ),
            params={
                "organization_id": organization_id,
                "connector_id": connector_id,
                "external_id": external_id,
                "display_name": display_name,
            },
        )
        group_id = int(group_result.scalar_one())
        await session.exec(
            text(
                """
                INSERT INTO connector_external_principals (
                    connector_id, principal_type, external_id, display_name, mapped_group_id
                ) VALUES (:connector_id, 'group', :external_id, :display_name, :group_id)
                ON CONFLICT (connector_id, principal_type, external_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    mapped_group_id = EXCLUDED.mapped_group_id,
                    updated_at = now()
                """
            ),
            params={
                "connector_id": connector_id,
                "external_id": external_id,
                "display_name": display_name,
                "group_id": group_id,
            },
        )
        return group_id

    async def _mapped_user_id(
        self,
        session: Any,
        connector_id: int,
        organization_id: int,
        external_id: str,
    ) -> int | None:
        query: Any = text(
            """
            SELECT COALESCE(mapping.mapped_user_id, account.id) AS user_id
            FROM (SELECT 1) AS seed
            LEFT JOIN connector_external_principals AS mapping
              ON mapping.connector_id = :connector_id
             AND mapping.principal_type = 'user'
             AND mapping.external_id = :external_id
            LEFT JOIN organization_members AS member ON member.organization_id = :organization_id
            LEFT JOIN "user" AS account
              ON account.id = member.user_id AND lower(account.email) = lower(:external_id)
            WHERE mapping.mapped_user_id IS NOT NULL OR account.id IS NOT NULL
            LIMIT 1
            """
        )
        value = (
            await session.exec(
                query,
                params={
                    "connector_id": connector_id,
                    "organization_id": organization_id,
                    "external_id": external_id,
                },
            )
        ).scalar_one_or_none()
        return int(value) if value is not None else None


external_acl_service = ExternalACLService()
