"""LLM model registry with pre-initialized instances."""

from typing import (
    Any,
    Dict,
    List,
)

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import logger

_DEEPSEEK_API_KEY = SecretStr(settings.DEEPSEEK_API_KEY)


def _create_llm(model_name: str, **overrides: Any) -> BaseChatModel:
    """Create a chat model backed by the official DeepSeek API."""
    model_options: Dict[str, Any] = {
        "model": model_name,
        "api_key": _DEEPSEEK_API_KEY,
        "base_url": settings.DEEPSEEK_BASE_URL,
        "max_tokens": settings.MAX_TOKENS,
        "extra_body": {
            "thinking": {"type": "enabled" if settings.DEEPSEEK_THINKING_ENABLED else "disabled"}
        },
    }
    if not settings.DEEPSEEK_THINKING_ENABLED:
        model_options["temperature"] = settings.DEFAULT_LLM_TEMPERATURE
    model_options.update(overrides)
    return ChatOpenAI(**model_options)


class LLMRegistry:
    """Registry of available LLM models with pre-initialized instances.

    This class maintains a list of LLM configurations and provides
    methods to retrieve them by name with optional argument overrides.
    """

    LLMS: List[Dict[str, Any]] = [
        {
            "name": "deepseek-v4-flash",
            "llm": _create_llm("deepseek-v4-flash"),
        },
    ]

    @classmethod
    def get(cls, model_name: str, **kwargs) -> BaseChatModel:
        """Get an LLM by name with optional argument overrides.

        When kwargs are provided a fresh ChatOpenAI instance is returned with
        those overrides applied, leaving the shared registry entry untouched.

        Args:
            model_name: Name of the model to retrieve.
            **kwargs: Optional arguments to override default model configuration.

        Returns:
            BaseChatModel instance.

        Raises:
            ValueError: If model_name is not found in LLMS.
        """
        model_entry = next((e for e in cls.LLMS if e["name"] == model_name), None)

        if not model_entry:
            available = ", ".join(e["name"] for e in cls.LLMS)
            raise ValueError(f"model '{model_name}' not found in registry. available models: {available}")

        if kwargs:
            logger.debug("creating_llm_with_custom_args", model_name=model_name, custom_args=list(kwargs.keys()))
            return _create_llm(model_name, **kwargs)

        logger.debug("using_default_llm_instance", model_name=model_name)
        return model_entry["llm"]

    @classmethod
    def get_all_names(cls) -> List[str]:
        """Return all registered model names in order.

        Returns:
            List of model name strings.
        """
        return [e["name"] for e in cls.LLMS]

    @classmethod
    def get_model_at_index(cls, index: int) -> Dict[str, Any]:
        """Return the model entry at a specific index, wrapping to 0 if out of range.

        Args:
            index: Index into LLMS.

        Returns:
            Model entry dict.
        """
        if 0 <= index < len(cls.LLMS):
            return cls.LLMS[index]
        return cls.LLMS[0]
