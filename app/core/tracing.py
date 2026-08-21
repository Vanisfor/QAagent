"""Lightweight in-process tracing for performance, call chains, tokens and errors."""

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Iterator, Literal

from asgi_correlation_id import correlation_id

from app.core.config import settings
from app.core.logging import logger
from app.core.token_usage import TokenUsage
from app.core.trace_writer import JsonlTraceWriter

TraceStatus = Literal["ok", "error", "cancelled"]
AttributeValue = str | int | float | bool

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_span_stack: ContextVar[tuple[str, ...]] = ContextVar("span_stack", default=())
_writer = JsonlTraceWriter(settings.TRACE_DIR, settings.TRACE_FILE_RETENTION_DAYS)

_ALLOWED_ATTRIBUTES = {
    "method",
    "route",
    "status_code",
    "streaming",
    "resumed",
    "tool_call_count",
    "tool_name",
    "attempt_count",
    "model",
    "top_k",
    "hit_count",
    "similarity_threshold",
    "batch_size",
    "dimensions",
    "chunk_count",
    "time_to_first_token_ms",
    "cache_hit",
    "result_count",
    "detached",
    "source_count",
    "inserted_count",
    "client_disconnected",
}


def current_trace_id() -> str | None:
    """Return the trace ID bound to the current context."""
    return _trace_id.get()


def current_span_id() -> str | None:
    """Return the active parent span ID, if any."""
    stack = _span_stack.get()
    return stack[-1] if stack else None


def close_trace_writer() -> None:
    """Flush and stop the process-local trace writer."""
    _writer.close()


def _new_id() -> str:
    return uuid.uuid4().hex


def _safe_error_message(error: BaseException) -> str:
    """Return a content-free diagnostic message for a known error class."""
    name = type(error).__name__.lower()
    if "timeout" in name:
        return "operation timed out"
    if "ratelimit" in name:
        return "provider rate limited"
    if "connection" in name or "operational" in name:
        return "connection failed"
    if "cancel" in name:
        return "operation cancelled"
    return "operation failed"


def _classify_error(error: BaseException) -> tuple[str, bool]:
    name = type(error).__name__
    lowered = name.lower()
    if "cancel" in lowered:
        return "stream_cancelled", False
    if "timeout" in lowered:
        return "llm_timeout", True
    if "ratelimit" in lowered:
        return "llm_rate_limited", True
    if "connection" in lowered or "operational" in lowered:
        return "database_unavailable", True
    return "unknown_error", False


class TraceSpan:
    """Mutable span state finalized by ``trace_span``."""

    def __init__(self, name: str, trace_id: str, span_id: str, parent_span_id: str | None) -> None:
        """Initialize a running span."""
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.started_at = datetime.now(UTC)
        self.started_ns = time.perf_counter_ns()
        self.status: TraceStatus = "ok"
        self.attributes: dict[str, AttributeValue] = {}
        self.tokens: TokenUsage | None = None
        self.error: dict[str, Any] | None = None

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        """Set an approved low-cardinality attribute."""
        if key in _ALLOWED_ATTRIBUTES:
            self.attributes[key] = value

    def set_tokens(self, usage: TokenUsage | None) -> None:
        """Attach normalized provider token usage."""
        self.tokens = usage

    def record_error(self, error: BaseException, *, code: str | None = None, retryable: bool | None = None) -> None:
        """Attach a sanitized error without raising it."""
        inferred_code, inferred_retryable = _classify_error(error)
        self.status = "cancelled" if inferred_code == "stream_cancelled" else "error"
        self.error = {
            "type": type(error).__name__,
            "code": code or inferred_code,
            "message": _safe_error_message(error),
            "retryable": inferred_retryable if retryable is None else retryable,
            "attempt": self.attributes.get("attempt_count"),
        }

    def finish(self) -> None:
        """Finalize and export the span without affecting business logic."""
        duration_ms = round((time.perf_counter_ns() - self.started_ns) / 1_000_000, 3)
        record = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "duration_ms": duration_ms,
            "tokens": self.tokens.to_dict() if self.tokens else None,
            "error": self.error,
            "attributes": self.attributes,
        }
        try:
            _writer.write(record)
        except Exception as error:
            logger.warning("trace_write_failed", error_type=type(error).__name__)
        try:
            from app.core.metrics import record_trace_span

            record_trace_span(self.name, self.status, duration_ms, self.tokens, self.error, self.attributes)
        except Exception as error:
            logger.warning("trace_metrics_failed", error_type=type(error).__name__)
        if duration_ms >= settings.TRACE_SLOW_SPAN_THRESHOLD_MS:
            logger.warning("slow_trace_span", span_name=self.name, duration_ms=duration_ms, status=self.status)


@contextmanager
def trace_span(
    name: str,
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    **attributes: AttributeValue,
) -> Iterator[TraceSpan]:
    """Create a failure-isolated span and preserve parent context across awaits."""
    resolved_trace_id = trace_id or _trace_id.get() or correlation_id.get() or _new_id()
    stack = _span_stack.get()
    resolved_parent = stack[-1] if stack else parent_span_id
    span = TraceSpan(name=name, trace_id=resolved_trace_id, span_id=_new_id(), parent_span_id=resolved_parent)
    for key, value in attributes.items():
        span.set_attribute(key, value)

    if not settings.TRACING_ENABLED:
        yield span
        return

    trace_token = _trace_id.set(resolved_trace_id)
    stack_token = _span_stack.set((*stack, span.span_id))
    try:
        yield span
    except BaseException as error:
        span.record_error(error)
        raise
    finally:
        _span_stack.reset(stack_token)
        _trace_id.reset(trace_token)
        span.finish()
