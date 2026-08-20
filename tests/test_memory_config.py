"""Configuration tests for long-term memory providers."""

import asyncio
from unittest.mock import AsyncMock, patch

from mem0 import AsyncMemory

from app.core.config import settings
from app.services.memory import MemoryService


def test_memory_uses_deepseek_and_siliconflow() -> None:
    """mem0 should keep DeepSeek chat and SiliconFlow embeddings separate."""
    factory = AsyncMock(return_value=object())

    with patch.object(AsyncMemory, "from_config", factory):
        asyncio.run(MemoryService().initialize())

    config = factory.await_args.kwargs["config_dict"]
    assert config["llm"]["provider"] == "deepseek"
    assert config["llm"]["config"] == {
        "model": settings.LONG_TERM_MEMORY_MODEL,
        "api_key": settings.DEEPSEEK_API_KEY,
        "deepseek_base_url": settings.DEEPSEEK_BASE_URL,
        "max_tokens": settings.MAX_TOKENS,
    }
    assert config["embedder"]["config"] == {
        "model": settings.LONG_TERM_MEMORY_EMBEDDER_MODEL,
        "api_key": settings.SILICONFLOW_API_KEY,
        "openai_base_url": settings.SILICONFLOW_BASE_URL,
        "embedding_dims": settings.EMBEDDING_DIM,
    }
