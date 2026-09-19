"""Validated current-user profile, appearance and Agent preferences."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictInput(BaseModel):
    """Reject accidental or malicious ownership and unknown configuration fields."""

    model_config = ConfigDict(extra="forbid")


class ProfileInput(StrictInput):
    """Editable presentation fields; identity email is intentionally separate."""

    display_name: str = Field(min_length=1, max_length=50)
    bio: str = Field(default="", max_length=500)
    language: Literal["auto", "zh", "en"] = "auto"
    timezone: str = Field(default="Asia/Shanghai", max_length=64)

    @field_validator("display_name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        """Disallow blank names while retaining international display names."""
        if not value.strip():
            raise ValueError("Display name cannot be blank")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Accept only real IANA timezones."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Unknown timezone")
        return value


class ProfileResponse(ProfileInput):
    """Profile projection without stored filesystem paths."""

    avatar_url: str | None = None


class CurrentUser(BaseModel):
    """Safe authenticated identity information."""

    id: int
    email: str
    status: str
    created_at: datetime
    last_login_at: datetime | None
    profile: ProfileResponse


class BackgroundSettings(StrictInput):
    """Bounded background preferences compatible with the existing UI."""

    preset: Literal["none", "green", "sunset", "ocean", "lavender", "charcoal", "custom"] = "none"
    imageDataUrl: str | None = Field(default=None, max_length=2_800_000)
    brightness: float = Field(default=1, ge=0.4, le=1.5)
    blur: float = Field(default=0, ge=0, le=20)
    opacity: float = Field(default=1, ge=0.3, le=1)

    @field_validator("imageDataUrl")
    @classmethod
    def image_prefix(cls, value: str | None) -> str | None:
        """Allow only bounded raster image data, never remote or script URLs."""
        if value is not None and not value.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")):
            raise ValueError("Unsupported background image")
        return value


class AppearanceSettings(StrictInput):
    """Persisted UI settings; BYOK stays in its existing encrypted table."""

    theme: Literal["light", "dark"] = "light"
    sidebar_collapsed: bool = False
    background: BackgroundSettings = Field(default_factory=BackgroundSettings)


class ResponseStyle(StrictInput):
    """Supported response style controls."""

    markdown: bool = True
    emoji: bool = False
    technical_depth: Literal["low", "medium", "high"] = "medium"


class Personalization(StrictInput):
    """User-controlled preferences subordinate to system and tool policy."""

    personality: Literal["professional", "friendly", "direct"] = "professional"
    custom_instructions: str = Field(default="", max_length=2000)
    verbosity: Literal["concise", "balanced", "detailed"] = "balanced"
    response_style: ResponseStyle = Field(default_factory=ResponseStyle)
    memory_enabled: bool = True


class ProfilePatch(StrictInput):
    """Partial editable profile update, validated again after merging."""

    display_name: str | None = Field(default=None, min_length=1, max_length=50)
    bio: str | None = Field(default=None, max_length=500)
    language: Literal["auto", "zh", "en"] | None = None
    timezone: str | None = Field(default=None, max_length=64)


class AppearancePatch(StrictInput):
    """Partial appearance update; background is one complete domain object."""

    theme: Literal["light", "dark"] | None = None
    sidebar_collapsed: bool | None = None
    background: BackgroundSettings | None = None


class PersonalizationPatch(StrictInput):
    """Partial Agent preference update."""

    personality: Literal["professional", "friendly", "direct"] | None = None
    custom_instructions: str | None = Field(default=None, max_length=2000)
    verbosity: Literal["concise", "balanced", "detailed"] | None = None
    response_style: ResponseStyle | None = None
    memory_enabled: bool | None = None
