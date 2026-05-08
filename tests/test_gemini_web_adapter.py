"""Tests for the gemini_web adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phdb.adapters.gemini_web import (
    GeminiWebAdapter,
    _parse_frontmatter,
    _parse_lineage,
    _parse_time,
    _url_to_slug,
)


# ── Synthetic landmark builders ───────────────────────────────────────────────

def _landmark(
    tmp_path: Path,
    url: str = "https://gemini.google.com/share/abc123",
    name: str = "Gemini - Test",
    created: str = "2026-04-17",
    blocks: list[tuple[str, str, str]] | None = None,  # (type, hhmm, content)
    lineage: str | None = None,
    filename: str = "test.md",
) -> Path:
    """Write a minimal landmark file and return its path."""
    lines = [
        "---",
        '"@context": "https://schema.org"',
        '"@type": "Conversation"',
        f"name: {name}",
        f"created: {created}",
        f'url: "{url}"',
        'description: "Gemini conversation with 4 messages"',
        "---",
    ]
    if lineage:
        lines.append(lineage)
        lines.append("")
    else:
        lines.append(f"[{url}]({url})")
        lines.append("")

    if blocks is None:
        blocks = [
            ("prompt", "07:35", "What is the answer?"),
            ("ai-response", "07:36", "The answer is 42."),
        ]

    for btype, ts, content in blocks:
        lines += [
            f"```ad-{btype}",
            f"title: (`{ts}`) {'Rob prompted' if btype == 'prompt' else 'Gemini responded'}",
            "",
            content,
            "```",
            "",
        ]

    p = tmp_path / filename
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_url_to_slug_share() -> None:
    assert _url_to_slug("https://gemini.google.com/share/b5681c2e0bb0") == "gemini-share-b5681c2e0bb0"


def test_url_to_slug_app() -> None:
    assert _url_to_slug("https://gemini.google.com/app/b1d6038265f1505b") == "gemini-app-b1d6038265f1505b"


def test_url_to_slug_invalid() -> None:
    assert _url_to_slug("https://example.com/other") is None


def test_parse_frontmatter_extracts_fields() -> None:
    text = '---\nname: Test\ncreated: 2026-04-17\nurl: "https://gemini.google.com/share/abc"\n---\nbody here'
    fm, body = _parse_frontmatter(text)
    assert fm["name"] == "Test"
    assert fm["created"] == "2026-04-17"
    assert fm["url"] == "https://gemini.google.com/share/abc"
    assert body.strip() == "body here"


def test_parse_frontmatter_no_fm() -> None:
    text = "no frontmatter"
    fm, body = _parse_frontmatter(text)
    assert fm == {}
    assert "no frontmatter" in body


def test_parse_lineage_trunk() -> None:
    body = "[https://gemini.google.com/share/abc](https://gemini.google.com/share/abc)\n\n```ad-prompt"
    lineage, depth = _parse_lineage(body)
    assert lineage is None
    assert depth == 0


def test_parse_lineage_branch1() -> None:
    body = "From Obsidian Vault Structure and Metadata\n\n```ad-prompt"
    lineage, depth = _parse_lineage(body)
    assert lineage == "From Obsidian Vault Structure and Metadata"
    assert depth == 1


def test_parse_lineage_branch2() -> None:
    body = "From Branch • Obsidian Vault Structure and Metadata\n\n```ad-prompt"
    lineage, depth = _parse_lineage(body)
    assert depth == 2


def test_parse_lineage_branch3() -> None:
    body = "From Branch • Branch • Obsidian Vault Structure and Metadata"
    lineage, depth = _parse_lineage(body)
    assert depth == 3


def test_parse_time_24h() -> None:
    assert _parse_time("07:35") == "07:35"


def test_parse_time_12h_am() -> None:
    assert _parse_time("4:23 AM") == "04:23"


def test_parse_time_12h_pm() -> None:
    assert _parse_time("4:46 PM") == "16:46"


def test_parse_time_noon() -> None:
    assert _parse_time("12:00 PM") == "12:00"


def test_parse_time_midnight() -> None:
    assert _parse_time("12:00 AM") == "00:00"


# ── Adapter integration tests ─────────────────────────────────────────────────

@pytest.fixture()
def adapter() -> GeminiWebAdapter:
    return GeminiWebAdapter()


def test_adapter_yields_two_rows(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path)
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 2


def test_adapter_roles(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path)
    rows = list(adapter.iter_rows(p))
    assert rows[0].role == "user"
    assert rows[1].role == "assistant"


def test_adapter_kind_is_message(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path)
    rows = list(adapter.iter_rows(p))
    assert all(r.kind == "message" for r in rows)


def test_adapter_body_text(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, blocks=[("prompt", "07:35", "What is the answer?")])
    rows = list(adapter.iter_rows(p))
    assert rows[0].body_text == "What is the answer?"


def test_adapter_date_sent_24h(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, created="2026-04-17",
                  blocks=[("prompt", "07:35", "hi")])
    rows = list(adapter.iter_rows(p))
    assert rows[0].date_sent == "2026-04-17T07:35:00"


def test_adapter_date_sent_12h_pm(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, created="2026-04-30",
                  blocks=[("prompt", "4:46 PM", "hi")])
    rows = list(adapter.iter_rows(p))
    assert rows[0].date_sent == "2026-04-30T16:46:00"


def test_adapter_date_sent_12h_am(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, created="2026-04-30",
                  blocks=[("prompt", "4:23 AM", "hi")])
    rows = list(adapter.iter_rows(p))
    assert rows[0].date_sent == "2026-04-30T04:23:00"


def test_adapter_thread_key_from_url(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, url="https://gemini.google.com/share/abc123")
    rows = list(adapter.iter_rows(p))
    assert all(r.thread_key == "gemini-share-abc123" for r in rows)


def test_adapter_thread_key_app_url(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, url="https://gemini.google.com/app/deadbeef0123")
    rows = list(adapter.iter_rows(p))
    assert all(r.thread_key == "gemini-app-deadbeef0123" for r in rows)


def test_adapter_raw_hash_unique_per_block(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path)
    rows = list(adapter.iter_rows(p))
    hashes = [r.raw_hash for r in rows]
    assert len(set(hashes)) == len(hashes)


def test_adapter_raw_hash_format(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, url="https://gemini.google.com/share/abc123")
    rows = list(adapter.iter_rows(p))
    assert rows[0].raw_hash == "gemini-web:gemini-share-abc123:0"
    assert rows[1].raw_hash == "gemini-web:gemini-share-abc123:1"


def test_adapter_thread_metadata_json(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, url="https://gemini.google.com/share/abc123",
                  name="Gemini - Test Session")
    rows = list(adapter.iter_rows(p))
    meta = json.loads(rows[0].thread_metadata)
    assert meta["url"] == "https://gemini.google.com/share/abc123"
    assert meta["name"] == "Gemini - Test Session"
    assert meta["depth"] == 0
    assert meta["lineage_string"] is None


def test_adapter_lineage_in_metadata(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    lineage = "From Obsidian Vault Structure and Metadata"
    p = _landmark(tmp_path, url="https://gemini.google.com/app/b1d6038265f1505b",
                  lineage=lineage)
    rows = list(adapter.iter_rows(p))
    meta = json.loads(rows[0].thread_metadata)
    assert meta["lineage_string"] == lineage
    assert meta["depth"] == 1


def test_adapter_branch2_depth(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    lineage = "From Branch • Obsidian Vault Structure and Metadata"
    p = _landmark(tmp_path, url="https://gemini.google.com/app/96619097ab06c28f",
                  lineage=lineage)
    rows = list(adapter.iter_rows(p))
    meta = json.loads(rows[0].thread_metadata)
    assert meta["depth"] == 2


def test_adapter_payload_is_block_text(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, blocks=[("prompt", "07:35", "hello")])
    rows = list(adapter.iter_rows(p))
    assert "07:35" in rows[0].payload
    assert "hello" in rows[0].payload


def test_adapter_model_is_none(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path)
    rows = list(adapter.iter_rows(p))
    assert all(r.model is None for r in rows)


def test_adapter_thread_cwd_is_none(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path)
    rows = list(adapter.iter_rows(p))
    assert all(r.thread_cwd is None for r in rows)


def test_adapter_all_rows_share_thread_key(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, blocks=[
        ("prompt", "07:00", "turn 1"),
        ("ai-response", "07:01", "response 1"),
        ("prompt", "07:02", "turn 2"),
        ("ai-response", "07:03", "response 2"),
    ])
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 4
    assert len({r.thread_key for r in rows}) == 1


def test_adapter_empty_file_yields_no_rows(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = tmp_path / "empty.md"
    p.write_text("---\nname: Empty\ncreated: 2026-01-01\nurl: \"\"\n---\nno blocks here", encoding="utf-8")
    rows = list(adapter.iter_rows(p))
    assert rows == []


def test_adapter_unclosed_final_block(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    """A block with no closing fence at EOF should still be yielded."""
    content = "\n".join([
        "---",
        'name: Test',
        'created: 2026-04-18',
        'url: "https://gemini.google.com/share/xyz"',
        "---",
        "",
        "```ad-ai-response",
        "title: (`20:06`) Gemini responded",
        "",
        "This is the AI response with no closing fence.",
    ])
    p = tmp_path / "unclosed.md"
    p.write_text(content, encoding="utf-8")
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 1
    assert rows[0].role == "assistant"
    assert "no closing fence" in rows[0].body_text


def test_adapter_multiblock_ordering(tmp_path: Path, adapter: GeminiWebAdapter) -> None:
    p = _landmark(tmp_path, created="2026-04-17", blocks=[
        ("prompt", "07:00", "first"),
        ("ai-response", "07:01", "second"),
        ("prompt", "07:02", "third"),
    ])
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 3
    assert rows[0].role == "user"
    assert rows[1].role == "assistant"
    assert rows[2].role == "user"
    assert rows[0].body_text == "first"
    assert rows[2].body_text == "third"
