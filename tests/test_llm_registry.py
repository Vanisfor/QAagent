"""Configuration tests for the default chat model."""

from app.core.config import settings
from app.services.llm.registry import LLMRegistry


def test_deepseek_flash_is_the_only_default_chat_model() -> None:
    """The agent should not silently fall back to another provider model."""
    assert LLMRegistry.get_all_names() == ["deepseek-v4-flash"]
    assert settings.DEFAULT_LLM_MODEL == "deepseek-v4-flash"


def test_deepseek_flash_uses_official_deepseek_endpoint() -> None:
    """The registry should pass the configured official endpoint to ChatOpenAI."""
    model = LLMRegistry.get("deepseek-v4-flash")

    assert model.model_name == "deepseek-v4-flash"
    assert str(model.openai_api_base).rstrip("/") == settings.DEEPSEEK_BASE_URL.rstrip("/")
    assert model.max_tokens == settings.MAX_TOKENS
    assert model.extra_body == {"thinking": {"type": "disabled"}}
