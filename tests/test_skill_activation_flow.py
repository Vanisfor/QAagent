"""Behavioral tests for safe Skill receipts and prompt assembly."""

from pathlib import Path

from app.core.prompts.assembly import build_system_prompt
from app.core.skills import tools as skill_tools
from app.core.skills.registry import SkillRegistry
from app.schemas.user_account import Personalization, ProfileResponse


def test_activate_skill_tool_returns_only_a_verifiable_receipt(monkeypatch, tmp_path: Path) -> None:
    """The model-visible tool result must never contain the Skill body or path."""
    root = tmp_path / "skills"
    path = root / "grounded" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: grounded\ndescription: Use grounded evidence.\nversion: 1\n---\nPRIVATE INSTRUCTIONS",
        encoding="utf-8",
    )
    registry = SkillRegistry([root])
    registry.discover()
    monkeypatch.setattr(skill_tools, "skill_registry", registry)

    receipt_text = skill_tools.activate_skill.invoke({"name": "grounded"})
    receipt = skill_tools.parse_activation_receipt(receipt_text)

    assert receipt.name == "grounded"
    assert receipt.digest
    assert "PRIVATE INSTRUCTIONS" not in receipt_text
    assert str(path) not in receipt_text


def test_prompt_places_trusted_skill_before_untrusted_user_data() -> None:
    """Deployment Skill instructions remain distinct from user-controlled preferences."""
    prompt = build_system_prompt(
        ProfileResponse(display_name="Alice"),
        Personalization(custom_instructions="USER DATA"),
        "",
        skills_prompt="# Available skills\n- grounded: Use grounded evidence.",
        trusted_skill="TRUSTED SKILL BODY",
    )

    assert prompt.index("# Trusted deployment Skill") < prompt.index("TRUSTED SKILL BODY")
    assert prompt.index("TRUSTED SKILL BODY") < prompt.index("# User preference data")
    assert prompt.index("USER DATA") < prompt.index("# Mandatory policy reminder")

