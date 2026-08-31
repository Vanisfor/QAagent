"""Boundary and deterministic sanitization for untrusted RAG evidence."""

import re
from typing import Iterable

from app.schemas.knowledge import KnowledgeHit

_INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(all\s+)?previous\s+instructions?"),
    re.compile(r"(?i)system\s*:\s*"),
    re.compile(r"(?i)(reveal|print|show)\s+(the\s+)?system\s+prompt"),
    re.compile(r"忽略(?:之前|以上|所有)?(?:的)?指令"),
    re.compile(r"(?:输出|显示|泄露).{0,8}系统提示"),
)


def sanitize_evidence_text(text: str) -> tuple[str, bool]:
    """Remove control characters, forged delimiters and obvious instructions."""
    sanitized = text.replace("\x00", "").replace("<evidence", "&lt;evidence").replace("</evidence", "&lt;/evidence")
    sanitized = sanitized.replace("<doc", "&lt;doc").replace("</doc", "&lt;/doc")
    risky = False
    for pattern in _INJECTION_PATTERNS:
        sanitized, count = pattern.subn("[untrusted instruction removed]", sanitized)
        risky = risky or count > 0
    return sanitized, risky


def build_evidence_block(hits: Iterable[KnowledgeHit]) -> str:
    """Build a strongly delimited evidence block with stable source IDs."""
    documents: list[str] = []
    for index, hit in enumerate(hits, start=1):
        content, risky = sanitize_evidence_text(hit.content)
        risk = "true" if risky else "false"
        relevance = hit.score if hit.score > 0 else hit.similarity
        documents.append(
            f'<doc id="{index}" source="{hit.source}" space="{hit.space_slug}" '
            f'relevance="{relevance:.2f}" risky="{risk}">\n'
            f"{content}\n</doc>"
        )
    return "<evidence>\n" + "\n\n".join(documents) + "\n</evidence>"
