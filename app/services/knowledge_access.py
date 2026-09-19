"""Resolve tenant and external-group access context from trusted database state."""

from typing import Any

from sqlalchemy import text

from app.schemas.knowledge import RetrievalContext
from app.services.database import database_service


class KnowledgeAccessDenied(Exception):
    """Raised when a user has no organization membership."""


class KnowledgeAccessService:
    """Build retrieval context without trusting model or client group claims."""

    async def context_for_user(
        self,
        user_id: int,
        *,
        requested_spaces: list[str] | tuple[str, ...] = (),
    ) -> RetrievalContext:
        """Resolve organization, group and valid space scope for one user."""
        query: Any = text(
            """
            SELECT member.organization_id,
                   COALESCE(array_agg(DISTINCT group_member.group_id)
                       FILTER (WHERE group_member.group_id IS NOT NULL), '{}') AS group_ids
            FROM organization_members AS member
            LEFT JOIN knowledge_groups AS knowledge_group
              ON knowledge_group.organization_id = member.organization_id
            LEFT JOIN knowledge_group_members AS group_member
              ON group_member.group_id = knowledge_group.id AND group_member.user_id = member.user_id
            WHERE member.user_id = :user_id
            GROUP BY member.organization_id
            ORDER BY member.organization_id
            """
        )
        async with database_service.session_factory() as session:
            rows = (await session.exec(query, params={"user_id": user_id})).mappings().all()
        if not rows:
            raise KnowledgeAccessDenied("user has no organization membership")
        organization_ids = tuple(int(row["organization_id"]) for row in rows)
        group_ids = tuple(dict.fromkeys(str(group_id) for row in rows for group_id in (row["group_ids"] or [])))

        requested = list(dict.fromkeys(space for space in requested_spaces if space))
        if requested:
            space_query: Any = text(
                """
                SELECT DISTINCT space.slug
                FROM knowledge_spaces AS space
                WHERE space.organization_id = ANY(CAST(:organization_ids AS bigint[]))
                  AND space.slug = ANY(CAST(:requested AS text[]))
                  AND (
                      space.is_public
                      OR EXISTS (
                          SELECT 1 FROM knowledge_space_principals AS acl
                          WHERE acl.space_id = space.id
                            AND (
                                (acl.principal_type = 'user' AND acl.principal_id = :user_principal)
                                OR (acl.principal_type = 'group' AND acl.principal_id = ANY(CAST(:group_ids AS text[])))
                            )
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM knowledge_documents AS document
                          JOIN knowledge_document_principals AS acl ON acl.document_id = document.id
                          WHERE document.space_id = space.id
                            AND document.deleted_at IS NULL
                            AND (
                                (acl.principal_type = 'user' AND acl.principal_id = :user_principal)
                                OR (acl.principal_type = 'group' AND acl.principal_id = ANY(CAST(:group_ids AS text[])))
                            )
                      )
                  )
                ORDER BY space.slug
                """
            )
            async with database_service.session_factory() as session:
                valid_spaces = [
                    str(row["slug"])
                    for row in (
                        await session.exec(
                            space_query,
                            params={
                                "organization_ids": list(organization_ids),
                                "requested": requested,
                                "user_principal": str(user_id),
                                "group_ids": list(group_ids),
                            },
                        )
                    )
                    .mappings()
                    .all()
                ]
        else:
            valid_spaces = []
        return RetrievalContext(
            user_id=str(user_id),
            organization_ids=organization_ids,
            group_ids=group_ids,
            space_slugs=tuple(valid_spaces),
            space_scope_requested=bool(requested),
        )


knowledge_access_service = KnowledgeAccessService()
