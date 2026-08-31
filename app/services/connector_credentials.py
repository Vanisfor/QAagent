"""Encrypted connector-token storage separated from user LLM credentials."""

import base64
import hashlib
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from app.core.config import settings
from app.services.database import database_service


class ConnectorCredentialUnavailable(Exception):
    """Raised when connector credential encryption or data is unavailable."""


class ConnectorCredentialService:
    """Encrypt connector tokens with a platform master key and connector-bound AAD."""

    def __init__(self, encryption_key: str) -> None:
        """Store the platform key without deriving material until needed."""
        self._encryption_key = encryption_key

    def _cipher(self) -> AESGCM:
        if len(self._encryption_key) < 32:
            raise ConnectorCredentialUnavailable("CONNECTOR_CREDENTIAL_ENCRYPTION_KEY is not configured")
        return AESGCM(hashlib.sha256(self._encryption_key.encode("utf-8")).digest())

    def _encrypt(self, connector_id: int, secret: str) -> str:
        nonce = os.urandom(12)
        payload = self._cipher().encrypt(nonce, secret.encode("utf-8"), str(connector_id).encode("ascii"))
        return "v1:" + base64.urlsafe_b64encode(nonce + payload).decode("ascii")

    def _decrypt(self, connector_id: int, payload: str) -> str:
        if not payload.startswith("v1:"):
            raise ConnectorCredentialUnavailable("connector credential format is unsupported")
        try:
            raw = base64.urlsafe_b64decode(payload[3:].encode("ascii"))
            return self._cipher().decrypt(raw[:12], raw[12:], str(connector_id).encode("ascii")).decode("utf-8")
        except (InvalidTag, ValueError) as error:
            raise ConnectorCredentialUnavailable("connector credential cannot be decrypted") from error

    async def save(self, connector_id: int, secret: str) -> None:
        """Encrypt and upsert one connector token."""
        if not secret.strip():
            raise ValueError("connector secret cannot be empty")
        statement: Any = text(
            """
            INSERT INTO knowledge_connector_credentials (connector_id, encrypted_secret)
            VALUES (:connector_id, :encrypted_secret)
            ON CONFLICT (connector_id) DO UPDATE
            SET encrypted_secret = EXCLUDED.encrypted_secret,
                key_version = 1,
                updated_at = now()
            """
        )
        async with database_service.session_factory() as session, session.begin():
            await session.exec(
                statement,
                params={"connector_id": connector_id, "encrypted_secret": self._encrypt(connector_id, secret.strip())},
            )

    async def get(self, connector_id: int) -> str:
        """Decrypt one connector token for a single synchronization run."""
        statement: Any = text(
            "SELECT encrypted_secret FROM knowledge_connector_credentials WHERE connector_id = :connector_id"
        )
        async with database_service.session_factory() as session:
            payload = (await session.exec(statement, params={"connector_id": connector_id})).scalar_one_or_none()
        if payload is None:
            raise ConnectorCredentialUnavailable(f"connector credential not found: {connector_id}")
        return self._decrypt(connector_id, str(payload))


connector_credential_service = ConnectorCredentialService(settings.CONNECTOR_CREDENTIAL_ENCRYPTION_KEY)
