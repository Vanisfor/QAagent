"""Provider-tolerant token usage extraction without recording content."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class TokenUsage:
    """Normalized token counters attached to an LLM span."""

    input: int = 0
    output: int = 0
    total: int = 0
    reasoning: int | None = None
    cache_hit_input: int | None = None
    cache_miss_input: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def _integer(mapping: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _first(*values: int | None) -> int | None:
    """Return the first present counter while preserving zero."""
    return next((value for value in values if value is not None), None)


def extract_token_usage(response: Any) -> TokenUsage | None:
    """Extract LangChain or OpenAI-compatible usage metadata from a response."""
    usage = getattr(response, "usage_metadata", None)
    response_metadata = getattr(response, "response_metadata", None) or {}
    if not isinstance(usage, dict):
        usage = response_metadata.get("token_usage") or response_metadata.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = _integer(usage, "input_tokens", "prompt_tokens") or 0
    output_tokens = _integer(usage, "output_tokens", "completion_tokens") or 0
    total_tokens = _integer(usage, "total_tokens") or input_tokens + output_tokens
    input_details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}
    if not isinstance(input_details, dict):
        input_details = {}
    if not isinstance(output_details, dict):
        output_details = {}

    return TokenUsage(
        input=input_tokens,
        output=output_tokens,
        total=total_tokens,
        reasoning=_first(_integer(output_details, "reasoning"), _integer(usage, "reasoning_tokens")),
        cache_hit_input=_first(
            _integer(input_details, "cache_read", "cache_hit"),
            _integer(usage, "prompt_cache_hit_tokens"),
        ),
        cache_miss_input=_first(
            _integer(input_details, "cache_miss"),
            _integer(usage, "prompt_cache_miss_tokens"),
        ),
    )


class TokenAccumulator:
    """Keep cumulative usage from the final streaming chunk without double counting."""

    def __init__(self) -> None:
        """Initialize with no observed usage."""
        self._usage: TokenUsage | None = None

    def observe(self, response: Any) -> None:
        """Observe one streaming response chunk."""
        usage = extract_token_usage(response)
        if usage is None:
            return
        if self._usage is None or usage.total >= self._usage.total:
            self._usage = usage

    @property
    def usage(self) -> TokenUsage | None:
        """Return the latest cumulative usage."""
        return self._usage
