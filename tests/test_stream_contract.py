"""Tests for the frontend-facing streaming contract."""

from pydantic import ValidationError
from pytest import raises

from app.schemas.chat import (
    ChatRequest,
    Message,
    StreamResponse,
)
from app.services.memory import MemoryService


def test_chat_request_accepts_supported_reasoning_effort() -> None:
    """Reasoning effort should be explicit and default to off."""
    messages = [Message(role="user", content="hello")]
    assert ChatRequest(messages=messages).reasoning_effort == "off"
    assert ChatRequest(messages=messages, reasoning_effort="max").reasoning_effort == "max"


def test_chat_request_rejects_unknown_reasoning_effort() -> None:
    """Unknown provider effort levels should fail validation."""
    with raises(ValidationError):
        ChatRequest(messages=[Message(role="user", content="hello")], reasoning_effort="medium")


def test_stream_response_supports_structured_usage_event() -> None:
    """SSE events should carry structured payloads without losing compatibility fields."""
    event = StreamResponse(type="usage", data={"input_tokens": 10, "output_tokens": 4})
    assert event.type == "usage"
    assert event.content == ""
    assert event.done is False


def test_memory_cache_stats_are_process_local() -> None:
    """The UI cache metric starts from a truthful zero baseline."""
    stats = MemoryService().cache_stats()
    assert stats == {"hits": 0, "misses": 0, "hit_rate": 0.0, "scope": "instance"}
