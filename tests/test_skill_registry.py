"""Regression tests for bounded Skill discovery and trusted activation."""

from pathlib import Path

import pytest

from app.core.skills.registry import SkillRegistry


def _write_skill(path: Path, *, name: str, description: str, body: str, version: str = "1") -> None:
    """Write one representative deployment-managed Skill."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: {version}\n---\n{body}",
        encoding="utf-8",
    )


def test_discovery_keeps_body_out_of_catalog_and_activation_receipt(tmp_path: Path) -> None:
    """Only the trusted prompt assembler may load a Skill body."""
    root = tmp_path / "skills"
    _write_skill(root / "alpha" / "SKILL.md", name="alpha", description="Alpha workflow", body="SECRET BODY")
    _write_skill(root / "beta" / "SKILL.md", name="beta", description="Beta workflow", body="SECOND BODY")

    registry = SkillRegistry([root])
    summaries = registry.discover()
    activation = registry.activate("alpha")

    assert [skill.name for skill in summaries] == ["alpha", "beta"]
    assert "SECRET BODY" not in registry.build_skills_prompt()
    assert not hasattr(activation, "instructions")
    assert registry.load_instructions(activation) == "SECRET BODY"
    assert registry.build_skills_prompt().index("alpha") < registry.build_skills_prompt().index("beta")


def test_frontmatter_limit_does_not_reject_a_large_bounded_body(tmp_path: Path) -> None:
    """Metadata scanning must stop at the closing delimiter instead of reading the body."""
    root = tmp_path / "skills"
    body = "a" * (70 * 1024)
    _write_skill(root / "large" / "SKILL.md", name="large", description="Large bounded workflow", body=body)

    registry = SkillRegistry([root], max_skill_tokens=20_000)

    assert [skill.name for skill in registry.discover()] == ["large"]


def test_duplicate_names_fail_discovery_instead_of_shadowing(tmp_path: Path) -> None:
    """Deployment order must never silently decide which Skill is trusted."""
    root = tmp_path / "skills"
    _write_skill(root / "one" / "SKILL.md", name="same", description="First", body="first")
    _write_skill(root / "two" / "SKILL.md", name="same", description="Second", body="second")

    with pytest.raises(ValueError, match="Duplicate Skill name"):
        SkillRegistry([root]).discover()


def test_discovery_skips_malformed_or_unsafe_metadata(tmp_path: Path) -> None:
    """Invalid metadata cannot enter the trusted system-prompt catalog."""
    root = tmp_path / "skills"
    _write_skill(root / "valid" / "SKILL.md", name="valid-skill", description="Valid workflow", body="body")
    _write_skill(root / "unsafe" / "SKILL.md", name="unsafe name", description="Unsafe", body="body")
    (root / "missing" / "SKILL.md").parent.mkdir(parents=True)
    (root / "missing" / "SKILL.md").write_text("---\nname: missing-description\n---\nbody", encoding="utf-8")

    registry = SkillRegistry([root])

    assert [skill.name for skill in registry.discover()] == ["valid-skill"]


def test_activation_fails_if_the_file_changes_after_discovery(tmp_path: Path) -> None:
    """A receipt cannot load a different body after a rolling or local file change."""
    root = tmp_path / "skills"
    path = root / "stable" / "SKILL.md"
    _write_skill(path, name="stable", description="Stable workflow", body="original")
    registry = SkillRegistry([root])
    registry.discover()
    activation = registry.activate("stable")
    path.write_text(path.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")

    with pytest.raises(ValueError, match="digest"):
        registry.load_instructions(activation)


def test_activation_rejects_unknown_name_and_excessive_body_tokens(tmp_path: Path) -> None:
    """Unknown and over-budget Skills fail closed before reaching the model."""
    root = tmp_path / "skills"
    _write_skill(root / "large" / "SKILL.md", name="large", description="Large", body="x" * 100)
    registry = SkillRegistry([root], max_skill_tokens=5)

    assert registry.discover() == []
    with pytest.raises(KeyError):
        registry.activate("large")
    with pytest.raises(KeyError):
        registry.activate("../large")

