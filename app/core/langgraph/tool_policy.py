"""Per-tool execution safety policies."""

from dataclasses import dataclass
from enum import StrEnum


class ToolIdempotency(StrEnum):
    """Classify whether a tool can be retried safely."""

    READ_ONLY = "read_only"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


@dataclass(frozen=True)
class ToolPolicy:
    """Timeout, retry, and idempotency policy for one tool."""

    timeout_seconds: float | None
    max_attempts: int
    idempotency: ToolIdempotency


TOOL_POLICIES: dict[str, ToolPolicy] = {
    "knowledge_search": ToolPolicy(10.0, 2, ToolIdempotency.READ_ONLY),
    "duckduckgo_results_json": ToolPolicy(15.0, 2, ToolIdempotency.READ_ONLY),
    "ask_human": ToolPolicy(None, 1, ToolIdempotency.NON_IDEMPOTENT),
}

DEFAULT_TOOL_POLICY = ToolPolicy(10.0, 1, ToolIdempotency.NON_IDEMPOTENT)


def get_tool_policy(tool_name: str) -> ToolPolicy:
    """Return a fail-safe policy for a registered tool name."""
    return TOOL_POLICIES.get(tool_name, DEFAULT_TOOL_POLICY)
