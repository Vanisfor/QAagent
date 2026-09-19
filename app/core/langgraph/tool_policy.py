"""Per-tool execution safety policies."""

from dataclasses import dataclass
from enum import StrEnum


class ToolIdempotency(StrEnum):
    """Classify whether a tool can be retried safely."""

    READ_ONLY = "read_only"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


class ToolBudgetClass(StrEnum):
    """Classify independent per-turn execution budgets."""

    CONTROL = "control"
    READ_ONLY = "read_only"
    INTERACTIVE = "interactive"
    SIDE_EFFECT = "side_effect"


@dataclass(frozen=True)
class ToolPolicy:
    """Timeout, retry, and idempotency policy for one tool."""

    timeout_seconds: float | None
    max_attempts: int
    idempotency: ToolIdempotency
    budget_class: ToolBudgetClass


TOOL_POLICIES: dict[str, ToolPolicy] = {
    "knowledge_search": ToolPolicy(10.0, 2, ToolIdempotency.READ_ONLY, ToolBudgetClass.READ_ONLY),
    "duckduckgo_results_json": ToolPolicy(15.0, 2, ToolIdempotency.READ_ONLY, ToolBudgetClass.READ_ONLY),
    "ask_human": ToolPolicy(None, 1, ToolIdempotency.NON_IDEMPOTENT, ToolBudgetClass.INTERACTIVE),
    "activate_skill": ToolPolicy(5.0, 1, ToolIdempotency.READ_ONLY, ToolBudgetClass.CONTROL),
}

DEFAULT_TOOL_POLICY = ToolPolicy(10.0, 1, ToolIdempotency.NON_IDEMPOTENT, ToolBudgetClass.SIDE_EFFECT)


def get_tool_policy(tool_name: str) -> ToolPolicy:
    """Return a fail-safe policy for a registered tool name."""
    return TOOL_POLICIES.get(tool_name, DEFAULT_TOOL_POLICY)
