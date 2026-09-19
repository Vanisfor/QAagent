"""Model-visible Skill activation with content-free receipts."""

import json
from typing import Any

from langchain_core.tools import tool

from app.core.skills.registry import SkillActivation, skill_registry
from app.core.metrics import skill_activation_total

_ACTIVATION_TYPE = "skill_activation"


def encode_activation_receipt(activation: SkillActivation) -> str:
    """Serialize only the immutable identity needed by the next Agent node."""
    return json.dumps(
        {
            "type": _ACTIVATION_TYPE,
            "name": activation.name,
            "digest": activation.digest,
            "version": activation.version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_activation_receipt(content: str) -> SkillActivation:
    """Parse a receipt only from an actual activate_skill ToolMessage."""
    try:
        payload: Any = json.loads(content)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid Skill activation receipt") from error
    if not isinstance(payload, dict) or set(payload) != {"type", "name", "digest", "version"}:
        raise ValueError("Invalid Skill activation receipt")
    if payload.get("type") != _ACTIVATION_TYPE:
        raise ValueError("Invalid Skill activation receipt")
    if not all(isinstance(payload.get(key), str) and payload[key] for key in ("name", "digest", "version")):
        raise ValueError("Invalid Skill activation receipt")
    return SkillActivation(name=payload["name"], digest=payload["digest"], version=payload["version"])


@tool
def activate_skill(name: str) -> str:
    """Activate one reviewed deployment Skill by exact name and return a safe receipt."""
    try:
        receipt = encode_activation_receipt(skill_registry.activate(name))
    except Exception:
        skill_activation_total.labels(status="rejected").inc()
        raise
    skill_activation_total.labels(status="activated").inc()
    return receipt
