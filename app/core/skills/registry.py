"""Discover reviewed Skills and load their instructions through verified receipts."""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import logger

_DEFAULT_MAX_METADATA_BYTES = 64 * 1024
_DEFAULT_MAX_SKILL_BYTES = 512 * 1024
_DEFAULT_MAX_SKILL_TOKENS = 4_000
_DEFAULT_MAX_CATALOG_TOKENS = 2_000
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _estimate_tokens(value: str) -> int:
    """Return a conservative local estimate without provider vocabulary I/O."""
    return max(1, (len(value.encode("utf-8")) + 3) // 4)


@dataclass(frozen=True, slots=True)
class Skill:
    """Validated metadata for one immutable deployment-managed Skill."""

    name: str
    description: str
    version: str
    digest: str
    path: Path
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SkillActivation:
    """Content-free reference produced by the model-visible activation tool."""

    name: str
    digest: str
    version: str


class SkillRegistry:
    """Own trusted Skill roots, catalog metadata and digest-checked loading."""

    def __init__(
        self,
        roots: Iterable[str | Path] | None = None,
        *,
        max_metadata_bytes: int = _DEFAULT_MAX_METADATA_BYTES,
        max_skill_bytes: int = _DEFAULT_MAX_SKILL_BYTES,
        max_skill_tokens: int = _DEFAULT_MAX_SKILL_TOKENS,
        max_catalog_tokens: int = _DEFAULT_MAX_CATALOG_TOKENS,
    ) -> None:
        """Initialize configuration without reading files."""
        self.roots = self._resolve_roots(roots or ["skills"])
        self.max_metadata_bytes = max(256, max_metadata_bytes)
        self.max_skill_bytes = max(1_024, max_skill_bytes)
        self.max_skill_tokens = max(1, max_skill_tokens)
        self.max_catalog_tokens = max(1, max_catalog_tokens)
        self._skills: dict[str, Skill] = {}

    def configure(
        self,
        roots: Iterable[str | Path],
        *,
        max_metadata_bytes: int,
        max_skill_bytes: int,
        max_skill_tokens: int,
        max_catalog_tokens: int,
    ) -> None:
        """Apply startup configuration before discovery."""
        self.roots = self._resolve_roots(roots)
        self.max_metadata_bytes = max(256, max_metadata_bytes)
        self.max_skill_bytes = max(1_024, max_skill_bytes)
        self.max_skill_tokens = max(1, max_skill_tokens)
        self.max_catalog_tokens = max(1, max_catalog_tokens)
        self._skills = {}

    @property
    def skills(self) -> tuple[Skill, ...]:
        """Return deterministic summaries from the most recent discovery."""
        return tuple(self._skills[name] for name in sorted(self._skills))

    @property
    def catalog_digest(self) -> str:
        """Return a stable content digest for readiness and rollout checks."""
        payload = "\n".join(f"{skill.name}:{skill.version}:{skill.digest}" for skill in self.skills)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def discover(self) -> list[Skill]:
        """Validate Skill files and build a bounded, deterministic catalog."""
        discovered: dict[str, Skill] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            candidates = sorted(
                candidate
                for candidate in root.rglob("*")
                if candidate.is_file() and candidate.name.casefold() == "skill.md"
            )
            for candidate in candidates:
                try:
                    skill = self._inspect(candidate, root)
                except (OSError, UnicodeError, ValueError) as error:
                    logger.warning(
                        "skill_metadata_skipped",
                        path=str(candidate),
                        error_type=type(error).__name__,
                    )
                    continue
                if skill.name in discovered:
                    raise ValueError(f"Duplicate Skill name: {skill.name}")
                discovered[skill.name] = skill

        prompt = self._format_catalog(discovered.values())
        if prompt and _estimate_tokens(prompt) > self.max_catalog_tokens:
            raise ValueError("Skill catalog exceeds configured token limit")
        self._skills = discovered
        logger.info(
            "skills_discovered",
            skill_count=len(discovered),
            root_count=len(self.roots),
            catalog_digest=self.catalog_digest[:12],
        )
        return list(self.skills)

    def build_skills_prompt(self) -> str:
        """Format reviewed summaries for routing without loading instructions."""
        return self._format_catalog(self.skills)

    def activate(self, name: str) -> SkillActivation:
        """Return a content-free immutable reference after exact-name lookup."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Skill name is required")
        skill = self._skills.get(name.strip())
        if skill is None:
            raise KeyError(f"Unknown Skill: {name}")
        return SkillActivation(name=skill.name, digest=skill.digest, version=skill.version)

    def load_instructions(self, activation: SkillActivation) -> str:
        """Load only the body referenced by a matching catalog digest."""
        skill = self._skills.get(activation.name)
        if skill is None:
            raise KeyError(f"Unknown Skill: {activation.name}")
        if activation.digest != skill.digest or activation.version != skill.version:
            raise ValueError("Skill activation digest or version does not match the catalog")
        path = skill.path.resolve(strict=True)
        if not any(self._within(path, root) for root in self.roots):
            raise ValueError("Skill path is outside configured roots")
        raw = self._read_bounded(path)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != activation.digest:
            raise ValueError("Skill file digest changed after discovery")
        _, body = self._split_document(raw)
        if _estimate_tokens(body) > self.max_skill_tokens:
            raise ValueError("Skill body exceeds configured token limit")
        return body

    def missing_required(self, required: Iterable[str]) -> tuple[str, ...]:
        """Return required names absent from the current catalog."""
        return tuple(sorted({name for name in required if name not in self._skills}))

    def _inspect(self, path: Path, root: Path) -> Skill:
        """Validate one file without retaining its instruction body."""
        if path.is_symlink():
            raise ValueError("Skill files cannot be symbolic links")
        resolved = path.resolve(strict=True)
        if not self._within(resolved, root):
            raise ValueError("Skill path is outside configured roots")
        raw = self._read_bounded(resolved)
        metadata_text, body = self._split_document(raw)
        metadata = self._parse_frontmatter(metadata_text)
        name = self._required_name(metadata)
        description = self._required_description(metadata)
        version = self._version(metadata)
        if _estimate_tokens(body) > self.max_skill_tokens:
            raise ValueError("Skill body exceeds configured token limit")
        return Skill(
            name=name,
            description=description,
            version=version,
            digest=hashlib.sha256(raw).hexdigest(),
            path=resolved,
            metadata=metadata,
        )

    def _read_bounded(self, path: Path) -> bytes:
        """Read a file only after enforcing its total byte bound."""
        if path.stat().st_size > self.max_skill_bytes:
            raise ValueError("Skill file is too large")
        return path.read_bytes()

    def _split_document(self, raw: bytes) -> tuple[str, str]:
        """Split bounded frontmatter from the body without scanning body as metadata."""
        lines = raw.splitlines(keepends=True)
        if not lines or lines[0].rstrip(b"\r\n") != b"---":
            raise ValueError("SKILL.md must start with an exact frontmatter delimiter")
        metadata_size = len(lines[0])
        metadata_lines: list[bytes] = []
        body_offset = len(lines[0])
        for line in lines[1:]:
            metadata_size += len(line)
            body_offset += len(line)
            if metadata_size > self.max_metadata_bytes:
                raise ValueError("Skill metadata is too large")
            if line.rstrip(b"\r\n") == b"---":
                metadata_text = b"".join(metadata_lines).decode("utf-8")
                body = raw[body_offset:].decode("utf-8").strip()
                return metadata_text, body
            metadata_lines.append(line)
        raise ValueError("SKILL.md frontmatter is not closed")

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, Any]:
        """Parse a deliberately small YAML-compatible scalar subset."""
        values: dict[str, Any] = {}
        nested_metadata: dict[str, str] = {}
        in_metadata = False
        for raw_line in text.splitlines():
            if "\t" in raw_line:
                raise ValueError("Skill metadata cannot contain tabs")
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            indented = raw_line.startswith(" ")
            line = raw_line.strip()
            if ":" not in line:
                raise ValueError("Skill metadata entries must be key-value pairs")
            key, raw_value = (part.strip() for part in line.split(":", 1))
            if not _KEY.fullmatch(key):
                raise ValueError("Skill metadata contains an invalid key")
            value = SkillRegistry._unquote(raw_value)
            if indented:
                if not in_metadata or not value:
                    raise ValueError("Only scalar metadata children are supported")
                if key in nested_metadata:
                    raise ValueError(f"Duplicate Skill metadata key: {key}")
                nested_metadata[key] = value
                continue
            in_metadata = key == "metadata" and not value
            if key in values:
                raise ValueError(f"Duplicate Skill metadata key: {key}")
            if in_metadata:
                values[key] = nested_metadata
            elif not value:
                raise ValueError(f"Skill metadata value is required: {key}")
            else:
                values[key] = value
        return values

    @staticmethod
    def _required_name(metadata: dict[str, Any]) -> str:
        """Validate the stable identifier used in tool calls and receipts."""
        value = metadata.get("name")
        if not isinstance(value, str) or not _SKILL_NAME.fullmatch(value):
            raise ValueError("Skill name must be a lowercase ASCII slug")
        return value

    @staticmethod
    def _required_description(metadata: dict[str, Any]) -> str:
        """Validate the single-line system-prompt catalog description."""
        value = metadata.get("description")
        if not isinstance(value, str) or not value or len(value) > 300:
            raise ValueError("Skill description must contain 1-300 characters")
        if any(ord(character) < 32 for character in value):
            raise ValueError("Skill description cannot contain control characters")
        return value

    @staticmethod
    def _version(metadata: dict[str, Any]) -> str:
        """Return one bounded immutable version label."""
        value = metadata.get("version", "1")
        if not isinstance(value, str) or not _VERSION.fullmatch(value):
            raise ValueError("Skill version is invalid")
        return value

    @staticmethod
    def _format_catalog(skills: Iterable[Skill]) -> str:
        """Build a content-free routing catalog from validated metadata."""
        ordered = sorted(skills, key=lambda skill: skill.name)
        if not ordered:
            return ""
        lines = [
            "# Available deployment Skills",
            "Use a Skill only when its description matches the user's task.",
        ]
        lines.extend(f"- {skill.name}: {skill.description}" for skill in ordered)
        lines.append("Activate a matching Skill by its exact name before following it.")
        return "\n".join(lines)

    @staticmethod
    def _resolve_roots(roots: Iterable[str | Path]) -> tuple[Path, ...]:
        """Resolve roots once so process working-directory drift cannot change scope."""
        return tuple(Path(root).expanduser().resolve() for root in roots)

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        """Check path containment without trusting client-controlled strings."""
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _unquote(value: str) -> str:
        """Accept ordinary quoted or plain scalar metadata values."""
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            return value[1:-1]
        return value


skill_registry = SkillRegistry()
