"""Security and concurrency boundaries for per-user BYOK settings."""

import asyncio
from datetime import (
    UTC,
    datetime,
)

import pytest

from app.core.config import settings
from app.models.user_llm_settings import UserLLMSettings
from app.schemas.settings import LLMSettingsInput
from app.services.llm.service import LLMService
from app.services.llm.registry import LLMRegistry
from app.services.user_llm_settings import (
    UserLLMRuntimeConfig,
    UserLLMSettingsNotConfigured,
    UserLLMSettingsService,
    UserLLMSettingsUnavailable,
    UserLLMSettingsValidationError,
)


def _runtime(api_key: str) -> UserLLMRuntimeConfig:
    return UserLLMRuntimeConfig(
        provider="deepseek",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key=api_key,
        temperature=0.2,
        max_tokens=2000,
        thinking_enabled=False,
    )


def test_api_key_encryption_is_user_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ciphertext must not expose plaintext or decrypt for a different user."""
    monkeypatch.setattr(settings, "USER_SETTINGS_ENCRYPTION_KEY", "test-secret-with-at-least-32-characters")
    service = UserLLMSettingsService()

    encrypted = service._encrypt(11, "sk-user-a-secret")

    assert "sk-user-a-secret" not in encrypted
    assert service._decrypt(11, encrypted) == "sk-user-a-secret"
    with pytest.raises(UserLLMSettingsUnavailable):
        service._decrypt(12, encrypted)


def test_public_settings_never_return_api_key() -> None:
    """The response exposes only a masked suffix, never ciphertext or plaintext."""
    row = UserLLMSettings(
        user_id=7,
        provider="deepseek",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        encrypted_api_key="v1:ciphertext",
        api_key_last_four="c123",
        temperature=0.2,
        max_tokens=2000,
        thinking_enabled=False,
        validated_at=datetime.now(UTC),
    )

    response = UserLLMSettingsService._to_response(row)
    serialized = response.model_dump_json()

    assert response.api_key_masked == "••••c123"
    assert "ciphertext" not in serialized
    assert "encrypted_api_key" not in serialized


def test_failed_validation_does_not_open_persistence_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Save must stop before any database write when provider validation fails."""
    monkeypatch.setattr(settings, "USER_SETTINGS_ENCRYPTION_KEY", "test-secret-with-at-least-32-characters")
    service = UserLLMSettingsService()
    persistence_opened = False

    async def fail_validation(user_id: int, payload: LLMSettingsInput) -> UserLLMRuntimeConfig:
        raise UserLLMSettingsValidationError("validation failed")

    def forbidden_session_factory():
        nonlocal persistence_opened
        persistence_opened = True
        raise AssertionError("persistence must not start")

    monkeypatch.setattr(service, "validate", fail_validation)
    monkeypatch.setattr("app.services.user_llm_settings.database_service.session_factory", forbidden_session_factory)

    with pytest.raises(UserLLMSettingsValidationError):
        asyncio.run(service.save(3, LLMSettingsInput(api_key="sk-invalid-key")))

    assert persistence_opened is False


def test_missing_user_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user without BYOK settings must not fall back to the platform key."""
    service = UserLLMSettingsService()

    async def no_row(user_id: int) -> None:
        return None

    monkeypatch.setattr(service, "_get_row", no_row)

    with pytest.raises(UserLLMSettingsNotConfigured):
        asyncio.run(service.get_runtime(99))


def test_request_scoped_llm_instances_keep_user_credentials_separate() -> None:
    """Creating two user services must produce independent model clients."""
    service_a = LLMService(_runtime("sk-user-a-secret"))
    service_b = LLMService(_runtime("sk-user-b-secret"))

    model_a = service_a.get_llm()
    model_b = service_b.get_llm()

    assert model_a is not model_b
    assert model_a.openai_api_key.get_secret_value() == "sk-user-a-secret"
    assert model_b.openai_api_key.get_secret_value() == "sk-user-b-secret"


def test_user_llm_initialization_never_falls_back_to_platform_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request-scoped construction failure must fail closed."""

    def fail_get(model_name: str, **kwargs):
        raise ValueError("model construction failed")

    monkeypatch.setattr(LLMRegistry, "get", fail_get)

    with pytest.raises(ValueError, match="model construction failed"):
        LLMService(_runtime("sk-user-secret"))
