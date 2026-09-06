"""Structure-aware document chunking shared by ingestion paths."""

import re
from dataclasses import dataclass
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.schemas.knowledge import DocumentChunk


_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_UNDERLINE_HEADING = re.compile(r"^([=\-`:'\"~^_*+#<>])\1{2,}\s*$")
_MARKDOWN_FORMATS = {".md", ".markdown", "markdown"}
_RST_FORMATS = {".rst", "rst"}
_SPLIT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", " ", ""]


@dataclass(frozen=True)
class _Section:
    """One document section before size-bounded splitting."""

    path: tuple[str, ...]
    level: int
    body: str


def estimate_token_count(text: str) -> int:
    """Estimate mixed Chinese/English tokens locally without model downloads."""
    if not text:
        return 0

    count = 0
    ascii_bytes = 0
    for character in text:
        if character.isascii():
            ascii_bytes += len(character.encode("utf-8"))
            continue
        if ascii_bytes:
            count += (ascii_bytes + 3) // 4
            ascii_bytes = 0
        count += 1
    if ascii_bytes:
        count += (ascii_bytes + 3) // 4
    return max(1, count)


def _clean_heading(value: str) -> str:
    """Normalize a heading for paths and repeated chunk context."""
    return re.sub(r"\s+", " ", value.strip()).strip("# ")


def _with_document_title(document_title: str | None, path: tuple[str, ...]) -> tuple[str, ...]:
    """Add the document title as the path root without duplicating an H1."""
    title = _clean_heading(document_title or "")
    if not title:
        return path
    if path and path[0].casefold() == title.casefold():
        return path
    return (title, *path)


def _set_heading(stack: list[tuple[int, str]], level: int, title: str) -> None:
    """Replace the active heading branch at ``level``."""
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, title))


def _split_sections(content: str, *, document_title: str | None, format_hint: str | None) -> list[_Section]:
    """Split Markdown/RST on headings while preserving their hierarchy."""
    normalized_format = (format_hint or "").lower()
    structured = normalized_format in _MARKDOWN_FORMATS or normalized_format in _RST_FORMATS
    if not structured:
        path = _with_document_title(document_title, ())
        return [_Section(path=path, level=0, body=content.strip())]

    lines = content.splitlines()
    sections: list[_Section] = []
    heading_stack: list[tuple[int, str]] = []
    body_lines: list[str] = []
    rst_levels: dict[str, int] = {}
    in_fence = False

    def flush() -> None:
        body = "\n".join(body_lines).strip()
        if body:
            heading_path = tuple(title for _, title in heading_stack)
            sections.append(
                _Section(
                    path=_with_document_title(document_title, heading_path),
                    level=heading_stack[-1][0] if heading_stack else 0,
                    body=body,
                )
            )
        body_lines.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if normalized_format in _MARKDOWN_FORMATS and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence = not in_fence
            body_lines.append(line)
            index += 1
            continue

        atx_match = _ATX_HEADING.match(stripped) if not in_fence and normalized_format in _MARKDOWN_FORMATS else None
        if atx_match:
            flush()
            _set_heading(heading_stack, len(atx_match.group(1)), _clean_heading(atx_match.group(2)))
            index += 1
            continue

        underline_match = None
        if not in_fence and stripped and index + 1 < len(lines):
            underline_match = _UNDERLINE_HEADING.match(lines[index + 1].strip())
            if (
                underline_match
                and normalized_format in _MARKDOWN_FORMATS
                and underline_match.group(1) not in {"=", "-"}
            ):
                underline_match = None
        if underline_match:
            flush()
            adornment = underline_match.group(1)
            if normalized_format in _MARKDOWN_FORMATS:
                level = 1 if adornment == "=" else 2
            else:
                level = rst_levels.setdefault(adornment, len(rst_levels) + 1)
            _set_heading(heading_stack, level, _clean_heading(stripped))
            index += 2
            continue

        body_lines.append(line)
        index += 1

    flush()
    if sections:
        return sections
    path = _with_document_title(document_title, ())
    return [_Section(path=path, level=0, body=content.strip())]


def _render_section(path: tuple[str, ...], body: str) -> str:
    """Repeat the heading breadcrumb so every embedded piece keeps context."""
    breadcrumb = " > ".join(path)
    return f"{breadcrumb}\n\n{body}" if breadcrumb else body


def _split_large_section(section: _Section, *, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Keep a small section whole or split its body on natural text boundaries."""
    rendered = _render_section(section.path, section.body)
    if estimate_token_count(rendered) <= chunk_size:
        return [rendered]

    prefix = " > ".join(section.path)
    prefix_tokens = estimate_token_count(prefix) + (1 if prefix else 0)
    body_budget = max(1, chunk_size - prefix_tokens)
    effective_overlap = min(chunk_overlap, max(0, body_budget - 1))
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=body_budget,
        chunk_overlap=effective_overlap,
        length_function=estimate_token_count,
        separators=_SPLIT_SEPARATORS,
        keep_separator=True,
    )
    bodies = [piece.strip() for piece in splitter.split_text(section.body) if piece.strip()]
    return [_render_section(section.path, body) for body in bodies]


def chunk_document(
    content: str,
    *,
    source: str,
    chunk_size: int,
    chunk_overlap: int,
    document_title: str | None = None,
    format_hint: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[DocumentChunk]:
    """Split a document by structure, then bound oversized sections by tokens."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError(f"chunk_overlap ({chunk_overlap}) must be smaller than chunk_size ({chunk_size})")
    if not content.strip():
        return []

    chunk_records: list[tuple[str, _Section, int, int]] = []
    sections = _split_sections(content, document_title=document_title, format_hint=format_hint)
    for section in sections:
        pieces = _split_large_section(section, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        for section_chunk_index, piece in enumerate(pieces):
            chunk_records.append((piece, section, section_chunk_index, len(pieces)))

    base_metadata = metadata or {}
    chunk_count = len(chunk_records)
    return [
        DocumentChunk(
            content=piece,
            source=source,
            metadata={
                **base_metadata,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "section_title": section.path[-1] if section.path else "",
                "section_path": list(section.path),
                "section_level": section.level,
                "section_chunk_index": section_chunk_index,
                "section_chunk_count": section_chunk_count,
                "estimated_token_count": estimate_token_count(piece),
                "chunking_strategy": "structure_then_token",
            },
        )
        for chunk_index, (piece, section, section_chunk_index, section_chunk_count) in enumerate(chunk_records)
    ]
