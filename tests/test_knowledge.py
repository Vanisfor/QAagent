"""Unit tests for the RAG knowledge base helpers.

These tests cover pure logic only — no database or embedding API required.
"""

import math

from pytest import raises
from scripts.ingest_docs import chunk_file

from app.core.config import settings
from app.schemas.knowledge import (
    DocumentChunk,
    KnowledgeHit,
)
from app.services.knowledge import (
    _content_hash,
    _validate_embeddings,
    _vector_literal,
)


def test_vector_literal_three_floats() -> None:
    """A list of floats renders as a bracket vector literal."""
    assert _vector_literal([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"


def test_vector_literal_integers() -> None:
    """Integers render without a decimal point."""
    assert _vector_literal([1, 2]) == "[1,2]"


def test_vector_literal_empty() -> None:
    """An empty vector renders as empty brackets."""
    assert _vector_literal([]) == "[]"


def test_content_hash_deterministic() -> None:
    """The same content always yields the same hash; different content differs."""
    assert _content_hash("hello") == _content_hash("hello")
    assert _content_hash("hello") != _content_hash("world")
    assert len(_content_hash("hello")) == 32


def test_validate_embeddings_rejects_wrong_count() -> None:
    """Provider output must contain one embedding per input text."""
    with raises(RuntimeError, match="embedding count mismatch"):
        _validate_embeddings([], expected_count=1)


def test_validate_embeddings_rejects_wrong_dimension() -> None:
    """Vectors incompatible with the pgvector column fail before insertion."""
    with raises(RuntimeError, match="embedding dimension mismatch"):
        _validate_embeddings([[0.0]], expected_count=1)


def test_validate_embeddings_rejects_non_finite_values() -> None:
    """NaN and infinity must not be stored in the vector index."""
    vector = [0.0] * settings.EMBEDDING_DIM
    vector[0] = math.nan
    with raises(RuntimeError, match="non-finite"):
        _validate_embeddings([vector], expected_count=1)


def test_document_chunk_model() -> None:
    """DocumentChunk validates content/source and defaults metadata."""
    chunk = DocumentChunk(content="hello world", source="a.md")
    assert chunk.content == "hello world"
    assert chunk.source == "a.md"
    assert chunk.metadata == {}


def test_document_chunk_with_metadata() -> None:
    """DocumentChunk accepts arbitrary JSON-serializable metadata."""
    chunk = DocumentChunk(content="c", source="s", metadata={"page": 1, "section": "intro"})
    assert chunk.metadata == {"page": 1, "section": "intro"}


def test_knowledge_hit_model() -> None:
    """KnowledgeHit stores retrieval results with a similarity score."""
    hit = KnowledgeHit(content="c", source="s", metadata={"page": 2}, similarity=0.912)
    assert hit.similarity == 0.912
    assert hit.metadata["page"] == 2


def test_chunk_file_splits_long_text(tmp_path) -> None:
    """chunk_file splits long documents into multiple chunks with the source label."""
    doc = tmp_path / "doc.md"
    doc.write_text("段落一。" + "很长" * 100 + "\n\n段落二。", encoding="utf-8")

    chunks = chunk_file(doc, chunk_size=50, chunk_overlap=5, source="doc.md")

    assert len(chunks) > 1
    assert all(c.source == "doc.md" for c in chunks)
    assert all(c.content.strip() for c in chunks)
    assert [c.metadata["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert all(c.metadata["chunk_count"] == len(chunks) for c in chunks)


def test_chunk_file_empty_file(tmp_path) -> None:
    """chunk_file returns no chunks for an empty file."""
    doc = tmp_path / "empty.md"
    doc.write_text("   \n  ", encoding="utf-8")

    assert chunk_file(doc, chunk_size=50, chunk_overlap=5, source="empty.md") == []


def test_chunk_file_rejects_invalid_overlap(tmp_path) -> None:
    """chunk_file raises when chunk_overlap is not smaller than chunk_size."""
    doc = tmp_path / "doc.md"
    doc.write_text("some content", encoding="utf-8")

    with raises(ValueError):
        chunk_file(doc, chunk_size=50, chunk_overlap=50, source="doc.md")
