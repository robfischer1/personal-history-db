"""Tests for the gemini_scribe adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phdb.adapters.gemini_scribe import GeminiScribeAdapter, _parse_yaml_list


# ── Synthetic Scribe file builder ─────────────────────────────────────────────

def _scribe(
    tmp_path: Path,
    session_id: str = "session_123_abc",
    name: str = "2026-04-16 Test Session",
    created: str = "2026-04-16",
    last_active: str = "2026-04-16T22:53:25-04:00",
    enabled_tools: list[str] | None = None,
    accessed_files: list[str] | None = None,
    blocks: list[tuple[str, str, str]] | None = None,
    filename: str = "test_scribe.md",
) -> Path:
    if enabled_tools is None:
        enabled_tools = ["read_only", "vault_ops"]
    if accessed_files is None:
        accessed_files = ["AGENTS.md"]
    if blocks is None:
        blocks = [
            ("prompt", "22:50", "Analyze my writing style."),
            ("ai-response", "22:53", "Here is your writing style guide."),
        ]

    lines = [
        "---",
        f"session_id: {session_id}",
        f"name: {name}",
        f"created: {created}",
        f"last_active: {last_active}",
        "enabled_tools:",
    ]
    for t in enabled_tools:
        lines.append(f'  - "{t}"')
    lines.append("accessed_files:")
    for f in accessed_files:
        lines.append(f'  - "{f}"')
    lines += [
        '"@context": "https://schema.org"',
        '"@type": "Conversation"',
        'author_type: "ai-assisted"',
        "---",
        f"## Agent Session {created}",
        "",
    ]

    for btype, ts, content in blocks:
        lines += [
            f"```ad-{btype}",
            f"title: (`{ts}`) {'Rob prompted' if btype == 'prompt' else 'Gemini Scribe responded'}",
            "",
            content,
            "```",
            "",
        ]

    p = tmp_path / filename
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_parse_yaml_list_simple() -> None:
    text = "---\nenabled_tools:\n  - read_only\n  - vault_ops\ncreated: 2026\n---"
    result = _parse_yaml_list(text, "enabled_tools")
    assert result == ["read_only", "vault_ops"]


def test_parse_yaml_list_quoted() -> None:
    text = '---\naccessed_files:\n  - "AGENTS.md"\n  - "SCHEMA.md"\ncreated: 2026\n---'
    result = _parse_yaml_list(text, "accessed_files")
    assert result == ["AGENTS.md", "SCHEMA.md"]


def test_parse_yaml_list_missing_key() -> None:
    text = "---\nname: Test\n---"
    result = _parse_yaml_list(text, "enabled_tools")
    assert result == []


# ── Adapter integration tests ─────────────────────────────────────────────────

@pytest.fixture()
def adapter() -> GeminiScribeAdapter:
    return GeminiScribeAdapter()


def test_adapter_yields_rows(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path)
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 2


def test_adapter_roles(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path)
    rows = list(adapter.iter_rows(p))
    assert rows[0].role == "user"
    assert rows[1].role == "assistant"


def test_adapter_kind_is_message(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path)
    rows = list(adapter.iter_rows(p))
    assert all(r.kind == "message" for r in rows)


def test_adapter_thread_key_from_session_id(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path, session_id="session_abc_xyz")
    rows = list(adapter.iter_rows(p))
    assert all(r.thread_key == "session_abc_xyz" for r in rows)


def test_adapter_raw_hash_format(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path, session_id="session_abc_xyz")
    rows = list(adapter.iter_rows(p))
    assert rows[0].raw_hash == "gemini-scribe:session_abc_xyz:0"
    assert rows[1].raw_hash == "gemini-scribe:session_abc_xyz:1"


def test_adapter_date_sent_24h(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path, created="2026-04-16",
                blocks=[("prompt", "22:50", "hello")])
    rows = list(adapter.iter_rows(p))
    assert rows[0].date_sent == "2026-04-16T22:50:00"


def test_adapter_body_text(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path, blocks=[("prompt", "22:50", "Analyze my writing.")])
    rows = list(adapter.iter_rows(p))
    assert rows[0].body_text == "Analyze my writing."


def test_adapter_thread_metadata_session_id(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path, session_id="session_1776393659260_mh61ecdro",
                last_active="2026-04-16T22:53:25-04:00")
    rows = list(adapter.iter_rows(p))
    meta = json.loads(rows[0].thread_metadata)
    assert meta["session_id"] == "session_1776393659260_mh61ecdro"
    assert meta["last_active"] == "2026-04-16T22:53:25-04:00"


def test_adapter_thread_metadata_enabled_tools(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path, enabled_tools=["read_only", "vault_ops", "external_mcp"])
    rows = list(adapter.iter_rows(p))
    meta = json.loads(rows[0].thread_metadata)
    assert "read_only" in meta["enabled_tools"]
    assert "external_mcp" in meta["enabled_tools"]


def test_adapter_thread_metadata_accessed_files(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path, accessed_files=["AGENTS.md", "SCHEMA.md"])
    rows = list(adapter.iter_rows(p))
    meta = json.loads(rows[0].thread_metadata)
    assert "AGENTS.md" in meta["accessed_files"]
    assert "SCHEMA.md" in meta["accessed_files"]


def test_adapter_source_kind(adapter: GeminiScribeAdapter) -> None:
    assert adapter.source_kind == "gemini-scribe"


def test_adapter_skips_tool_callouts(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    """Tool execution callout blocks between prompts should not produce rows."""
    content = "\n".join([
        "---",
        "session_id: session_test",
        "name: Test",
        "created: 2026-04-16",
        "last_active: 2026-04-16T22:53:25-04:00",
        "enabled_tools:",
        "  - read_only",
        "accessed_files:",
        "  - foo.md",
        '"@type": "Conversation"',
        "---",
        "## Agent Session 2026-04-16",
        "",
        "```ad-prompt",
        "title: (`22:50`) Rob prompted",
        "",
        "First prompt",
        "```",
        "",
        "> [!tools]- Tool Execution",
        "> 🔧 `read_file` path='foo.md' → success (10ms)",
        "",
        "```ad-ai-response",
        "title: (`22:51`) Gemini Scribe responded",
        "",
        "Response text",
        "```",
    ])
    p = tmp_path / "with_tools.md"
    p.write_text(content, encoding="utf-8")
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 2
    assert rows[0].body_text == "First prompt"
    assert rows[1].body_text == "Response text"


def test_adapter_unclosed_final_block(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    content = "\n".join([
        "---",
        "session_id: session_unclosed",
        "name: Unclosed Test",
        "created: 2026-04-16",
        "last_active: 2026-04-16T23:00:00-04:00",
        "enabled_tools:",
        "  - read_only",
        "accessed_files: []",
        '"@type": "Conversation"',
        "---",
        "",
        "```ad-prompt",
        "title: (`22:50`) Rob prompted",
        "",
        "The prompt",
        "```",
        "",
        "```ad-ai-response",
        "title: (`22:53`) Gemini Scribe responded",
        "",
        "Response without closing fence",
    ])
    p = tmp_path / "unclosed.md"
    p.write_text(content, encoding="utf-8")
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 2
    assert "closing fence" in rows[1].body_text


def test_adapter_all_rows_same_thread_key(tmp_path: Path, adapter: GeminiScribeAdapter) -> None:
    p = _scribe(tmp_path, session_id="sid-constant", blocks=[
        ("prompt", "22:00", "q1"),
        ("ai-response", "22:01", "a1"),
        ("prompt", "22:02", "q2"),
        ("ai-response", "22:03", "a2"),
    ])
    rows = list(adapter.iter_rows(p))
    assert all(r.thread_key == "sid-constant" for r in rows)
    assert len(rows) == 4
