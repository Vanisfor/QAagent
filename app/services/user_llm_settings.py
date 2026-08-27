"""Encrypted, fail-closed per-user LLM settings service."""

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from openai import (
    AsyncOpenAI,
    OpenAIError,
)
from sqlmodel import select

from app.core.config import settings
from app.core.logging import logger
from app.models.user_llm_settings import UserLLMSettings
from app.schemas.settings import (
    LLMSettingsInput,
    LLMSettingsResponse,
)
from app.services.database import database_service


class UserLLMSettingsError(Exception):
    """Base class for safe user-settings failures."""


class UserLLMSettingsUnavailable(UserLLMSettingsError):
    """Raised when server-side encryption is not configured."""


class UserLLMSettingsNotConfigured(UserLLMSettingsError):
    """Raised when a user has no active BYOK configuration."""


class UserLLMSettingsValidationError(UserLLMSettingsError):
    """Raised when a provider configuration cannot be validated."""


@dataclass(frozen=True)
class UserLLMRuntimeConfig:
    """Decrypted configuration used only for one runtime operation."""

    provider: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int
    thinking_enabled: bool


def _normalized_url(value: str) -> str:
    return value.rstrip("/")


class UserLLMSettingsService:
    """Validate and persist one encrypted LLM configuration per user."""

    def _cipher(self) -> AESGCM:
        secret = settings.USER_SETTINGS_ENCRYPTION_KEY
        if len(secret) < 32:
            raise UserLLMSettingsUnavailable("服务器尚未配置用户密钥加密，请联系管理员")
        return AESGCM(hashlib.sha256(secret.encode("utf-8")).digest())

    def _encrypt(self, user_id: int, api_key: str) -> str:
        nonce = os.urandom(12)
        encrypted = self._cipher().encrypt(nonce, api_key.encode("utf-8"), str(user_id).encode("ascii"))
        return "v1:" + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")

    def _decrypt(self, user_id: int, payload: str) -> str:
        if not payload.startswith("v1:"):
            raise UserLLMSettingsUnavailable("已保存的用户凭据格式不受支持")
        try:
            raw = base64.urlsafe_b64decode(payload[3:].encode("ascii"))
            decrypted = self._cipher().decrypt(raw[:12], raw[12:], str(user_id).encode("ascii"))
            return decrypted.decode("utf-8")
        except (InvalidTag, ValueError) as exc:
            logger.exception("user_llm_credential_decryption_failed", user_id=user_id)
            raise UserLLMSettingsUnavailable("已保存的用户凭据无法解密，请重新配置") from exc

    def _validate_policy(self, payload: LLMSettingsInput) -> str:
        base_url = _normalized_url(str(payload.base_url))
        allowed = {_normalized_url(value) for value in settings.ALLOWED_LLM_BASE_URLS}
        if base_url not in allowed:
            raise UserLLMSettingsValidationError("该 API 地址不在平台允许列表中")
        if payload.model not in {"deepseek-v4-flash"}:
            raise UserLLMSettingsValidationError("当前 Agent 暂不支持该模型")
        return base_url

    async def _get_row(self, user_id: int) -> UserLLMSettings | None:
        async with database_service.session_factory() as session:
            statement = select(UserLLMSettings).where(UserLLMSettings.user_id == user_id)
            return (await session.exec(statement)).first()

    async def _resolve_runtime(self, user_id: int, payload: LLMSettingsInput) -> UserLLMRuntimeConfig:
        base_url = self._validate_policy(payload)
        if payload.api_key is not None:
            api_key = payload.api_key.get_secret_value().strip()
        else:
            existing = await self._get_row(user_id)
            if existing is None:
                raise UserLLMSettingsNotConfigured("首次配置必须填写 API Key")
            api_key = self._decrypt(user_id, existing.encrypted_api_key)
        if not api_key:
            raise UserLLMSettingsValidationError("API Key 不能为空")
        return UserLLMRuntimeConfig(
            provider=payload.provider,
            model=payload.model,
            base_url=base_url,
            api_key=api_key,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            thinking_enabled=payload.thinking_enabled,
        )

    async def validate(self, user_id: int, payload: LLMSettingsInput) -> UserLLMRuntimeConfig:
        """Validate provider credentials without persisting them."""
        runtime = await self._resolve_runtime(user_id, payload)
        client = AsyncOpenAI(
            api_key=runtime.api_key,
            base_url=runtime.base_url,
            timeout=min(settings.LLM_TOTAL_TIMEOUT, 20),
            max_retries=0,
        )
        try:
            response = await client.models.list()
            available_models = {model.id for model in response.data}
            if runtime.model not in available_models:
                raise UserLLMSettingsValidationError("当前 API Key 无权使用所选模型")
            logger.info(
                "user_llm_settings_validated",
                user_id=user_id,
                provider=runtime.provider,
                model=runtime.model,
            )
            return runtime
        except UserLLMSettingsValidationError:
            raise
        except OpenAIError as exc:
            logger.warning(
                "user_llm_settings_validation_failed",
                user_id=user_id,
                provider=runtime.provider,
                model=runtime.model,
                error_type=type(exc).__name__,
            )
            raise UserLLMSettingsValidationError("连接验证失败，请检查 API 地址、API Key 和模型名称") from exc
        finally:
            await client.close()

    async def save(self, user_id: int, payload: LLMSettingsInput) -> LLMSettingsResponse:
        """Validate first, then atomically insert or update the active settings."""
        self._cipher()
        runtime = await self.validate(user_id, payload)
        now = datetime.now(UTC)
        encrypted_api_key = self._encrypt(user_id, runtime.api_key)

        async with database_service.session_factory() as session:
            statement = select(UserLLMSettings).where(UserLLMSettings.user_id == user_id)
            row = (await session.exec(statement)).first()
            if row is None:
                row = UserLLMSettings(
                    user_id=user_id,
                    provider=runtime.provider,
                    model=runtime.model,
                    base_url=runtime.base_url,
                    encrypted_api_key=encrypted_api_key,
                    api_key_last_four=runtime.api_key[-4:],
                    temperature=runtime.temperature,
                    max_tokens=runtime.max_tokens,
                    thinking_enabled=runtime.thinking_enabled,
                    validated_at=now,
                    updated_at=now,
                )
            else:
                row.provider = runtime.provider
                row.model = runtime.model
                row.base_url = runtime.base_url
                row.encrypted_api_key = encrypted_api_key
                row.api_key_last_four = runtime.api_key[-4:]
                row.temperature = runtime.temperature
                row.max_tokens = runtime.max_tokens
                row.thinking_enabled = runtime.thinking_enabled
                row.validated_at = now
                row.updated_at = now
            session.add(row)
            await session.commit()
            await session.refresh(row)

        logger.info("user_llm_settings_saved", user_id=user_id, provider=row.provider, model=row.model)
        return self._to_response(row)

    async def get_public(self, user_id: int) -> LLMSettingsResponse:
        """Return masked settings for the authenticated owner."""
        row = await self._get_row(user_id)
        if row is None:
            return LLMSettingsResponse(configured=False)
        return self._to_response(row)

    async def get_runtime(self, user_id: int) -> UserLLMRuntimeConfig:
        """Return decrypted runtime settings or fail closed when absent."""
        row = await self._get_row(user_id)
        if row is None:
            raise UserLLMSettingsNotConfigured("请先配置并验证 API Key，再开始聊天")
        return UserLLMRuntimeConfig(
            provider=row.provider,
            model=row.model,
            base_url=row.base_url,
            api_key=self._decrypt(user_id, row.encrypted_api_key),
            temperature=row.temperature,
            max_tokens=row.max_tokens,
            thinking_enabled=row.thinking_enabled,
        )

    async def delete(self, user_id: int) -> bool:
        """Delete the authenticated user's complete LLM configuration."""
        async with database_service.session_factory() as session:
            statement = select(UserLLMSettings).where(UserLLMSettings.user_id == user_id)
            row = (await session.exec(statement)).first()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
        logger.info("user_llm_settings_deleted", user_id=user_id)
        return True

    @staticmethod
    def _to_response(row: UserLLMSettings) -> LLMSettingsResponse:
        return LLMSettingsResponse(
            configured=True,
            provider="deepseek",
            model=row.model,
            base_url=row.base_url,
            api_key_masked=f"••••{row.api_key_last_four}",
            temperature=row.temperature,
            max_tokens=row.max_tokens,
            thinking_enabled=row.thinking_enabled,
            validated_at=row.validated_at,
        )


user_llm_settings_service = UserLLMSettingsService()
