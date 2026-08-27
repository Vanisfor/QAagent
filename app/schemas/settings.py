"""Schemas for authenticated user LLM settings."""

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
)

from app.schemas.base import BaseResponse


class LLMSettingsInput(BaseModel):
    """User-editable LLM settings; the API key may reuse an existing credential."""

    provider: Literal["deepseek"] = "deepseek"
    model: str = Field(default="deepseek-v4-flash", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    base_url: HttpUrl = Field(default=HttpUrl("https://api.deepseek.com"))
    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=512)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=2000, ge=1, le=8192)
    thinking_enabled: bool = False

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        """Remove accidental surrounding whitespace from model names."""
        return value.strip()


class LLMSettingsResponse(BaseResponse):
    """Public settings representation that never exposes the stored API key."""

    configured: bool
    provider: Literal["deepseek"] = "deepseek"
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    api_key_masked: str | None = None
    temperature: float = 0.2
    max_tokens: int = 2000
    thinking_enabled: bool = False
    validated_at: datetime | None = None


class LLMSettingsValidationResponse(BaseResponse):
    """Successful provider validation result."""

    valid: Literal[True] = True
    model: str
    message: str = "供应商连接验证成功"
