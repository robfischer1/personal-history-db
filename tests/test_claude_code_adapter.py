"""Tests for the claude_code adapter."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from types import SimpleNamespace
from phdb.plugins.claude_code import ClaudeCodePlugin
from phdb.formats.claude_code_jsonl import _derive_kind, _body_text_from_content


class ClaudeCodeAdapter:
    """Shim to maintain test compatibility for the ported claude_code plugin."""
    def __init__(self):
        # We don't actually need the manifest for the methods being tested
        self.plugin = ClaudeCodePlugin(None)

    def iter_rows(self, p: Path) -> Iterator[SimpleNamespace]:
        for rec in self.plugin.parse(p):
            yield SimpleNamespace(
                role=rec.role,
                kind=rec.kind,
                body_text=rec.body_text,
                is_bulk=0 if rec.kind == "message" else 1,
                tool_name=rec.tool_name,
                tool_use_id=rec.tool_use_id,
                model=rec.model,
                parent_uuid=rec.parent_uuid,
                raw_hash=rec.provenance.raw_hash,
                payload=rec.payload,
                thread_key=rec.thread_key,
                thread_metadata=json.dumps(rec.thread_metadata) if rec.thread_metadata else None,
                thread_cwd=rec.thread_metadata.get("cwd") if rec.thread_metadata else None,
                date_sent=rec.date_sent,
            )

    def compute_session_uuid(self, p: Path) -> str | None:
        from phdb.plugins.claude_code.plugin import _UUID_TAIL_RE
        m = _UUID_TAIL_RE.search(p.name)
        return m.group(1).lower() if m else None

    def validate_source_path(self, p: Path) -> None:
        self.plugin._validate_source_path(p)


# ── Synthetic JSONL builders ─────────────────────────────────────────────────

def _user_msg(uuid: str, parent: str | None, text: str,
              session_id: str = "sess-001",
              cwd: str = "/home/rob",
              ts: str = "2026-05-08T10:00:00.000Z") -> str:
    return json.dumps({
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": cwd,
        "version": "2.1.0",
        "gitBranch": "main",
        "userType": "external",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    })


def _assistant_msg(uuid: str, parent: str, text: str,
                   model: str = "claude-sonnet-4-6",
                   session_id: str = "sess-001",
                   ts: str = "2026-05-08T10:00:01.000Z") -> str:
    return json.dumps({
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": ts,
        "sessionId": session_id,
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
        },
    })


def _tool_use_msg(uuid: str, parent: str, tool_name: str, tool_id: str,
                  session_id: str = "sess-001",
                  ts: str = "2026-05-08T10:00:02.000Z") -> str:
    return json.dumps({
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": ts,
        "sessionId": session_id,
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [
                {"type": "tool_use", "id": tool_id,
                 "name": tool_name, "input": {"command": "ls"}},
            ],
        },
    })


def _tool_result_msg(uuid: str, parent: str, tool_id: str, result: str,
                     session_id: str = "sess-001",
                     ts: str = "2026-05-08T10:00:03.000Z") -> str:
    return json.dumps({
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": ts,
        "sessionId": session_id,
        "isSidechain": False,
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": result},
            ],
        },
    })


def _sidechain_msg(uuid: str, parent: str, thinking: str,
                   session_id: str = "sess-001",
                   ts: str = "2026-05-08T10:00:01.000Z") -> str:
    return json.dumps({
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": ts,
        "sessionId": session_id,
        "isSidechain": True,
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "thinking", "thinking": thinking}],
        },
    })


def _skip_lines() -> list[str]:
    return [
        json.dumps({"type": "queue-operation", "operation": "start"}),
        json.dumps({"type": "ai-title", "sessionId": "sess-001", "aiTitle": "Test"}),
        json.dumps({"type": "last-prompt", "sessionId": "sess-001"}),
        json.dumps({"type": "file-history-snapshot", "messageId": "x", "snapshot": {}}),
        json.dumps({"type": "attachment", "uuid": "att-001", "sessionId": "sess-001",
                    "cwd": "/home/rob", "version": "2.1.0", "gitBranch": "main"}),
    ]


def _make_jsonl(tmp_path: Path, lines: list[str], name: str = "sess-001.jsonl") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ── Unit tests for helpers ───────────────────────────────────────────────────

def test_body_text_from_text_content() -> None:
    content = [{"type": "text", "text": "hello world"}]
    assert _body_text_from_content(content) == "hello world"


def test_body_text_from_thinking_content() -> None:
    content = [{"type": "thinking", "thinking": "let me think"}]
    assert _body_text_from_content(content) == "let me think"


def test_body_text_from_mixed_content() -> None:
    content = [
        {"type": "thinking", "thinking": "plan"},
        {"type": "text", "text": "done"},
    ]
    assert _body_text_from_content(content) == "plan\ndone"


def test_body_text_empty_content() -> None:
    assert _body_text_from_content([]) is None
    assert _body_text_from_content(None) is None


def test_derive_kind_user_text() -> None:
    obj = {"type": "user", "isSidechain": False,
           "message": {"content": [{"type": "text", "text": "hi"}]}}
    kind, tname, tid = _derive_kind(obj)
    assert kind == "message"
    assert tname is None and tid is None


def test_derive_kind_assistant_text() -> None:
    obj = {"type": "assistant", "isSidechain": False,
           "message": {"content": [{"type": "text", "text": "hi back"}]}}
    kind, tname, tid = _derive_kind(obj)
    assert kind == "message"


def test_derive_kind_tool_use() -> None:
    obj = {"type": "assistant", "isSidechain": False,
           "message": {"content": [
               {"type": "tool_use", "id": "tid-1", "name": "Bash", "input": {}},
           ]}}
    kind, tname, tid = _derive_kind(obj)
    assert kind == "tool_use"
    assert tname == "Bash"
    assert tid == "tid-1"


def test_derive_kind_tool_result() -> None:
    obj = {"type": "user", "isSidechain": False,
           "message": {"content": [
               {"type": "tool_result", "tool_use_id": "tid-1", "content": "ok"},
           ]}}
    kind, tname, tid = _derive_kind(obj)
    assert kind == "tool_result"
    assert tid == "tid-1"


def test_derive_kind_sidechain() -> None:
    obj = {"type": "assistant", "isSidechain": True,
           "message": {"content": [{"type": "thinking", "thinking": "..."}]}}
    kind, _, _ = _derive_kind(obj)
    assert kind == "sidechain"


# ── Adapter integration tests ────────────────────────────────────────────────

@pytest.fixture()
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


def test_adapter_skips_non_turn_lines(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    p = _make_jsonl(tmp_path, _skip_lines())
    rows = list(adapter.iter_rows(p))
    assert rows == []


def test_adapter_yields_user_and_assistant(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [
        _user_msg("u1", None, "hello"),
        _assistant_msg("a1", "u1", "hello back"),
    ]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 2
    assert rows[0].role == "user"
    assert rows[1].role == "assistant"


def test_adapter_message_kind(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("u1", None, "hi")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].kind == "message"


def test_adapter_tool_use_kind_and_fields(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_tool_use_msg("a1", "u1", "Bash", "toolu-xyz")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].kind == "tool_use"
    assert rows[0].tool_name == "Bash"
    assert rows[0].tool_use_id == "toolu-xyz"
    assert rows[0].role == "assistant"


def test_adapter_tool_result_kind_and_fields(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_tool_result_msg("u2", "a1", "toolu-xyz", "exit 0")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].kind == "tool_result"
    assert rows[0].tool_use_id == "toolu-xyz"
    assert rows[0].role == "user"


def test_adapter_sidechain_kind(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_sidechain_msg("a0", "u1", "thinking...")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].kind == "sidechain"


def test_adapter_model_on_assistant(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_assistant_msg("a1", "u1", "hi", model="claude-opus-4-7")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].model == "claude-opus-4-7"


def test_adapter_model_none_on_user(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("u1", None, "hi")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].model is None


def test_adapter_parent_uuid(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [
        _user_msg("u1", None, "hello"),
        _assistant_msg("a1", "u1", "hello back"),
    ]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].parent_uuid is None
    assert rows[1].parent_uuid == "u1"


def test_adapter_raw_hash_uses_uuid(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("abc-123", None, "hi")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].raw_hash == "claude-code:abc-123"


def test_adapter_payload_is_json(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("u1", None, "hi")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    parsed = json.loads(rows[0].payload)
    assert parsed["uuid"] == "u1"


def test_adapter_thread_key_from_session_id(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("u1", None, "hi", session_id="my-session-id")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].thread_key == "my-session-id"


def test_adapter_thread_cwd(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("u1", None, "hi", cwd="/home/rob/project")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].thread_cwd == "/home/rob/project"


def test_adapter_thread_metadata_json(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("u1", None, "hi", session_id="s1", cwd="/home/rob")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    meta = json.loads(rows[0].thread_metadata)
    assert meta["sessionId"] == "s1"
    assert meta["cwd"] == "/home/rob"


def test_adapter_all_rows_share_thread_key(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [
        _user_msg("u1", None, "a", session_id="sid-x"),
        _assistant_msg("a1", "u1", "b", session_id="sid-x"),
        _tool_use_msg("a2", "a1", "Bash", "tid-1", session_id="sid-x"),
        _tool_result_msg("u2", "a2", "tid-1", "ok", session_id="sid-x"),
    ]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 4
    assert all(r.thread_key == "sid-x" for r in rows)


def test_adapter_skips_lines_without_uuid(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    # A user turn missing the uuid field should be skipped
    line = json.dumps({
        "type": "user",
        "timestamp": "2026-05-08T10:00:00.000Z",
        "sessionId": "s1",
        "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    })
    p = _make_jsonl(tmp_path, [line])
    rows = list(adapter.iter_rows(p))
    assert rows == []


def test_adapter_skips_malformed_json(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [
        "{not valid json",
        _user_msg("u1", None, "valid"),
    ]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert len(rows) == 1


def test_adapter_tool_chain_link(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    tid = "toolu-999"
    lines = [
        _tool_use_msg("a1", "u1", "Bash", tid),
        _tool_result_msg("u2", "a1", tid, "output text"),
    ]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    use_row = next(r for r in rows if r.kind == "tool_use")
    result_row = next(r for r in rows if r.kind == "tool_result")
    assert use_row.tool_use_id == result_row.tool_use_id == tid


def test_adapter_date_sent(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    ts = "2026-04-15T08:30:00.000Z"
    lines = [_user_msg("u1", None, "hi", ts=ts)]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].date_sent == ts


def test_adapter_body_text(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("u1", None, "What is the answer?")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].body_text == "What is the answer?"


# ── is_bulk embedding policy ─────────────────────────────────────────────────

def test_adapter_message_is_not_bulk(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_user_msg("u1", None, "hi")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].is_bulk == 0


def test_adapter_tool_use_is_bulk(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_tool_use_msg("a1", "u1", "Bash", "toolu-1")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].is_bulk == 1


def test_adapter_tool_result_is_bulk(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_tool_result_msg("u2", "a1", "toolu-1", "ok")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].is_bulk == 1


def test_adapter_sidechain_is_bulk(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_sidechain_msg("a0", "u1", "thinking about things")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].is_bulk == 1


def test_adapter_assistant_message_is_not_bulk(tmp_path: Path, adapter: ClaudeCodeAdapter) -> None:
    lines = [_assistant_msg("a1", "u1", "here is my response")]
    p = _make_jsonl(tmp_path, lines)
    rows = list(adapter.iter_rows(p))
    assert rows[0].is_bulk == 0


# ── Session UUID extraction (migration 0010 hook) ────────────────────────────

def test_compute_session_uuid_extracts_from_canonical_filename(adapter: ClaudeCodeAdapter) -> None:
    # D:\<records>\AI Sessions\Claude\claude-code__<encoded-cwd>__<uuid>.jsonl
    p = Path(r"D:\<records>\AI Sessions\Claude\claude-code__c--Users-<owner>-Obsidian__de8c7522-a9a2-4585-b6ef-b3e686e20290.jsonl")
    assert adapter.compute_session_uuid(p) == "de8c7522-a9a2-4585-b6ef-b3e686e20290"


def test_compute_session_uuid_extracts_from_legacy_filename(adapter: ClaudeCodeAdapter) -> None:
    # Legacy live path: <uuid>.jsonl with no prefix
    p = Path(r"C:\Users\<owner>\.claude\projects\c--Users-<owner>-Obsidian\4efecc8b-d706-4667-b922-7476858b2991.jsonl")
    assert adapter.compute_session_uuid(p) == "4efecc8b-d706-4667-b922-7476858b2991"


def test_compute_session_uuid_returns_none_for_agent_subsession(adapter: ClaudeCodeAdapter) -> None:
    # Agent sub-sessions are `agent-<hex>.jsonl` and have no session UUID.
    p = Path(r"D:\<records>\AI Sessions\Claude\claude-code__c--Users-<owner>-Obsidian__agent-a1709f28e260fa9fa.jsonl")
    assert adapter.compute_session_uuid(p) is None


def test_compute_session_uuid_returns_none_for_unrelated_filename(adapter: ClaudeCodeAdapter) -> None:
    p = Path("/tmp/sess-001.jsonl")
    assert adapter.compute_session_uuid(p) is None


def test_compute_session_uuid_lowercases(adapter: ClaudeCodeAdapter) -> None:
    p = Path(r"D:\<records>\DE8C7522-A9A2-4585-B6EF-B3E686E20290.jsonl")
    assert adapter.compute_session_uuid(p) == "de8c7522-a9a2-4585-b6ef-b3e686e20290"


# ── Legacy-path rejection (migration 0010 guard) ─────────────────────────────

def test_validate_source_path_rejects_live_claude_dir(adapter: ClaudeCodeAdapter) -> None:
    p = Path.home() / ".claude" / "projects" / "some-project" / "4efecc8b-d706-4667-b922-7476858b2991.jsonl"
    with pytest.raises(ValueError, match="refuses live .claude path"):
        adapter.validate_source_path(p)


def test_validate_source_path_accepts_non_claude_path(adapter: ClaudeCodeAdapter) -> None:
    p = Path.home() / "archives" / "ai-sessions" / "sess-001.jsonl"
    adapter.validate_source_path(p)


def test_validate_source_path_accepts_test_path(adapter: ClaudeCodeAdapter) -> None:
    # Tests under tmp_path must continue to pass validation.
    p = Path("/tmp/sess-001.jsonl")
    adapter.validate_source_path(p)
