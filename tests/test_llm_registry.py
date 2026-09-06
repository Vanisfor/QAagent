"""Configuration tests for the default chat model."""

import asyncio
import json
from typing import Any, cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.services.llm.registry import LLMRegistry
from app.services.llm.service import _JsonInstructionModel
from app.schemas.retrieval import QueryPlan


def test_deepseek_flash_is_the_only_default_chat_model() -> None:
    """The agent should not silently fall back to another provider model."""
    assert LLMRegistry.get_all_names() == ["deepseek-v4-flash"]
    assert settings.DEFAULT_LLM_MODEL == "deepseek-v4-flash"


def test_deepseek_flash_uses_official_deepseek_endpoint() -> None:
    """The registry should pass the configured official endpoint to ChatOpenAI."""
    model = cast(Any, LLMRegistry.get("deepseek-v4-flash"))

    assert model.model_name == "deepseek-v4-flash"
    assert str(model.openai_api_base).rstrip("/") == settings.DEEPSEEK_BASE_URL.rstrip("/")
    assert model.max_tokens == settings.MAX_TOKENS
    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_json_mode_receives_the_exact_canonical_schema() -> None:
    """DeepSeek JSON mode must see the same schema used by Pydantic validation."""

    class CapturingRunnable:
        def __init__(self) -> None:
            self.messages = []

        async def ainvoke(self, messages):
            self.messages = messages
            return QueryPlan(queries=["query"])

    capture = CapturingRunnable()
    model = _JsonInstructionModel(capture, QueryPlan)

    result = asyncio.run(model.ainvoke([HumanMessage(content="plan")]))

    assert isinstance(result, QueryPlan)
    assert isinstance(capture.messages[0], SystemMessage)
    expected_schema = json.dumps(QueryPlan.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    assert expected_schema in capture.messages[0].content
