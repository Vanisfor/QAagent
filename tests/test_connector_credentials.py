"""Tests for connector credential isolation and encryption binding."""

from pytest import raises

from app.services.connector_credentials import ConnectorCredentialService, ConnectorCredentialUnavailable


def test_connector_secret_is_bound_to_connector_id() -> None:
    """AES-GCM associated data prevents one connector from decrypting another token."""
    service = ConnectorCredentialService("x" * 32)
    payload = service._encrypt(7, "provider-token")

    assert service._decrypt(7, payload) == "provider-token"
    with raises(ConnectorCredentialUnavailable):
        service._decrypt(8, payload)
