"""Tests for the chunk_text function (pure, no mocks needed)."""

from __future__ import annotations

from phdb.embed_pipeline import (
    MIN_CHUNK_CHARS,
    OVERLAP_CHARS,
    TARGET_CHUNK_CHARS,
    chunk_text,
)


def test_empty_text() -> None:
    assert chunk_text("") == []


def test_whitespace_only() -> None:
    assert chunk_text("   \n\n  ") == []


def test_short_text_single_chunk() -> None:
    text = "Hello world, this is a short message."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_exact_threshold_single_chunk() -> None:
    text = "a" * TARGET_CHUNK_CHARS
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_one_over_threshold_splits() -> None:
    text = "a" * (TARGET_CHUNK_CHARS + 1)
    chunks = chunk_text(text)
    assert len(chunks) >= 2


def test_long_text_multiple_chunks() -> None:
    text = "word " * 2000
    chunks = chunk_text(text)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= TARGET_CHUNK_CHARS + 10


def test_paragraph_boundary_preferred() -> None:
    block = "x" * (TARGET_CHUNK_CHARS - 300)
    text = block + "\n\n" + "y" * 500
    chunks = chunk_text(text)
    assert len(chunks) >= 2
    assert chunks[0].endswith("x")


def test_newline_boundary_fallback() -> None:
    block = "x" * (TARGET_CHUNK_CHARS - 150)
    text = block + "\n" + "y" * 500
    chunks = chunk_text(text)
    assert len(chunks) >= 2


def test_sentence_boundary_fallback() -> None:
    block = "x" * (TARGET_CHUNK_CHARS - 150)
    text = block + ". " + "y" * 500
    chunks = chunk_text(text)
    assert len(chunks) >= 2


def test_space_boundary_fallback() -> None:
    block = "x" * (TARGET_CHUNK_CHARS - 50)
    text = block + " " + "y" * 200
    chunks = chunk_text(text)
    assert len(chunks) >= 2


def test_overlap_present() -> None:
    text = "word " * 2000
    chunks = chunk_text(text)
    assert len(chunks) >= 3
    for i in range(1, len(chunks)):
        tail_prev = chunks[i - 1][-OVERLAP_CHARS:]
        assert tail_prev in chunks[i] or chunks[i][:OVERLAP_CHARS] in chunks[i - 1]


def test_reassembly_covers_original() -> None:
    text = "The quick brown fox jumps over the lazy dog. " * 200
    chunks = chunk_text(text)
    joined = " ".join(chunks)
    for word in ["quick", "brown", "fox", "lazy", "dog"]:
        assert word in joined


def test_no_empty_chunks() -> None:
    text = "hello\n\n\n\nworld\n\n\n\n" * 300
    chunks = chunk_text(text)
    for c in chunks:
        assert len(c.strip()) > 0


def test_below_min_chunk_chars_still_works() -> None:
    text = "hi"
    assert len(text) < MIN_CHUNK_CHARS
    chunks = chunk_text(text)
    assert chunks == ["hi"]
