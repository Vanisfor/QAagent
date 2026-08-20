"""Tests for the local content-free tracing layer."""

import asyncio
from types import SimpleNamespace

from app.core import tracing
from app.core.token_usage import (
    TokenAccumulator,
    extract_token_usage,
)
from app.core.tracing import trace_span


class CaptureWriter:
    """In-memory writer used by tracing tests."""

    def __init__(self) -> None:
        """Initialize an empty record list."""
        self.records: list[dict] = []

    def write(self, record: dict) -> None:
        """Capture one record."""
        self.records.append(record)


def test_nested_spans_share_trace_and_preserve_parent(monkeypatch) -> None:
    """Nested spans should form a reconstructable call tree."""
    writer = CaptureWriter()
    monkeypatch.setattr(tracing, "_writer", writer)

    with trace_span("http.request", trace_id="trace-1", method="POST") as root:
        with trace_span("agent.run", streaming=False) as child:
            pass

    by_name = {record["name"]: record for record in writer.records}
    assert by_name["http.request"]["trace_id"] == "trace-1"
    assert by_name["agent.run"]["trace_id"] == "trace-1"
    assert child.parent_span_id == root.span_id


def test_unapproved_attributes_are_not_recorded(monkeypatch) -> None:
    """Prompt and document content must not enter trace attributes."""
    writer = CaptureWriter()
    monkeypatch.setattr(tracing, "_writer", writer)

    with trace_span("rag.search", top_k=5, prompt="private question"):
        pass

    assert writer.records[0]["attributes"] == {"top_k": 5}
    assert "private question" not in str(writer.records[0])


def test_errors_are_sanitized(monkeypatch) -> None:
    """Raw exception messages must not enter trace files."""
    writer = CaptureWriter()
    monkeypatch.setattr(tracing, "_writer", writer)

    try:
        with trace_span("llm.invoke", model="deepseek-v4-flash"):
            raise RuntimeError("Bearer token-value password=hunter2 sk-secretvalue")
    except RuntimeError:
        pass

    record = writer.records[0]
    assert record["status"] == "error"
    assert "token-value" not in record["error"]["message"]
    assert "hunter2" not in record["error"]["message"]
    assert "sk-secretvalue" not in record["error"]["message"]


def test_writer_failure_does_not_break_business_logic(monkeypatch) -> None:
    """Trace export failure must never fail a request."""

    class FailingWriter:
        def write(self, record: dict) -> None:
            raise OSError("disk unavailable")

    monkeypatch.setattr(tracing, "_writer", FailingWriter())
    with trace_span("agent.run"):
        result = "business-result"
    assert result == "business-result"


def test_token_usage_normalizes_provider_metadata() -> None:
    """DeepSeek-compatible usage should map to the stable token schema."""
    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 25,
            "total_tokens": 125,
            "input_token_details": {"cache_read": 10},
            "output_token_details": {"reasoning": 4},
        }
    )
    usage = extract_token_usage(response)
    assert usage is not None
    assert usage.to_dict() == {
        "input": 100,
        "output": 25,
        "total": 125,
        "reasoning": 4,
        "cache_hit_input": 10,
        "cache_miss_input": None,
    }


def test_streaming_usage_is_not_double_counted() -> None:
    """The accumulator should keep the largest cumulative final usage."""
    accumulator = TokenAccumulator()
    accumulator.observe(SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}))
    accumulator.observe(SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 8, "total_tokens": 18}))
    assert accumulator.usage is not None
    assert accumulator.usage.total == 18


def test_concurrent_tasks_do_not_share_trace_ids(monkeypatch) -> None:
    """ContextVar state must remain isolated across concurrent requests."""
    writer = CaptureWriter()
    monkeypatch.setattr(tracing, "_writer", writer)

    async def run_one(trace_id: str) -> None:
        with trace_span("http.request", trace_id=trace_id):
            await asyncio.sleep(0)
            with trace_span("agent.run"):
                await asyncio.sleep(0)

    async def run_all() -> None:
        await asyncio.gather(run_one("trace-a"), run_one("trace-b"))

    asyncio.run(run_all())
    grouped: dict[str, set[str]] = {}
    for record in writer.records:
        grouped.setdefault(record["trace_id"], set()).add(record["name"])
    assert grouped == {
        "trace-a": {"http.request", "agent.run"},
        "trace-b": {"http.request", "agent.run"},
    }
