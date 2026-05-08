"""Claude Code JSONL session adapter.

Ingests ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl files.
One thread row per JSONL file (thread_key = session UUID from filename or sessionId field).
One message row per user/assistant turn; other line types are skipped.

Kind mapping (derived from content, not the raw JSONL ``type`` field):
  'message'      — text turn (user or assistant)
  'tool_use'     — assistant turn whose content is (or starts with) a tool_use block
  'tool_result'  — user turn whose content contains a tool_result block
  'sidechain'    — isSidechain=True turn (thinking / extended thinking)
  'unknown'      — recognised turn type but unclassifiable content

Skipped line types: queue-operation, file-history-snapshot, last-prompt,
  ai-title, attachment, system, and anything else not in {user, assistant}.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy


# Line types that carry conversational content we want to store
_TURN_TYPES: frozenset[str] = frozenset({"user", "assistant"})

# Line types that carry useful thread-level metadata
_META_TYPES: frozenset[str] = frozenset({"user", "assistant", "attachment"})


def _extract_meta(obj: dict) -> dict[str, str | None]:
    """Pull session-level metadata fields present on most JSONL lines."""
    return {
        "sessionId": obj.get("sessionId"),
        "cwd": obj.get("cwd"),
        "version": obj.get("version"),
        "gitBranch": obj.get("gitBranch"),
        "userType": obj.get("userType"),
        "entrypoint": obj.get("entrypoint"),
    }


def _body_text_from_content(content: list | str | None) -> str | None:
    """Concatenate text fragments from a content list."""
    if not content:
        return None
    if isinstance(content, str):
        return content or None
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "text":
            text = item.get("text", "")
            if text:
                parts.append(text)
        elif itype == "thinking":
            text = item.get("thinking", "")
            if text:
                parts.append(text)
    return "\n".join(parts) if parts else None


def _derive_kind(obj: dict) -> tuple[str, str | None, str | None]:
    """Return (kind, tool_name, tool_use_id) for a turn."""
    line_type = obj.get("type", "")
    is_sidechain = bool(obj.get("isSidechain"))
    msg = obj.get("message", {})
    content = msg.get("content", [])

    if isinstance(content, str):
        return ("sidechain" if is_sidechain else "message"), None, None

    if not isinstance(content, list):
        return "unknown", None, None

    content_types = [c.get("type") for c in content if isinstance(c, dict)]

    # assistant turn with tool_use block
    if line_type == "assistant":
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                return "tool_use", item.get("name"), item.get("id")
        if is_sidechain or all(t in ("thinking", None) for t in content_types):
            return "sidechain", None, None
        return "message", None, None

    # user turn with tool_result block
    if line_type == "user":
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                return "tool_result", None, item.get("tool_use_id")
        return "message", None, None

    return "unknown", None, None


class ClaudeCodeAdapter(Adapter):
    """Ingester for Claude Code JSONL session files."""

    name = "claude_code"
    source_kind = "claude-code"
    file_kind = "jsonl"
    schema_type = "Conversation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        session_uuid = source_path.stem  # filename without .jsonl
        thread_key = session_uuid

        # Collect thread-level metadata from the first line that has it
        thread_meta: dict[str, str | None] = {}
        thread_cwd: str | None = None

        with source_path.open(encoding="utf-8") as fh:
            raw_lines = fh.readlines()

        # First pass: extract thread metadata
        for raw in raw_lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("type") in _META_TYPES:
                meta = _extract_meta(obj)
                # Use sessionId as thread_key when available
                if meta.get("sessionId"):
                    thread_key = meta["sessionId"]
                if not thread_cwd and meta.get("cwd"):
                    thread_cwd = meta["cwd"]
                if not thread_meta:
                    thread_meta = {k: v for k, v in meta.items() if v is not None}
                elif meta.get("sessionId") and not thread_meta.get("sessionId"):
                    thread_meta.update({k: v for k, v in meta.items() if v is not None})
                if thread_meta.get("sessionId") and thread_cwd:
                    break

        thread_metadata_json = json.dumps(thread_meta) if thread_meta else None

        # Second pass: yield message rows
        for raw in raw_lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue

            line_type = obj.get("type")
            if line_type not in _TURN_TYPES:
                continue

            turn_uuid = obj.get("uuid")
            if not turn_uuid:
                continue

            msg = obj.get("message", {}) or {}
            content = msg.get("content")

            kind, tool_name, tool_use_id = _derive_kind(obj)

            role: str | None = None
            if line_type == "user":
                role = "user"
            elif line_type == "assistant":
                role = "assistant"

            body_text = _body_text_from_content(content)

            model: str | None = msg.get("model") if line_type == "assistant" else None

            yield AdapterRow(
                schema_type="Conversation",
                date_sent=obj.get("timestamp"),
                body_text=body_text,
                is_bulk=0 if kind == "message" else 1,
                raw_hash=f"claude-code:{turn_uuid}",
                # AI session fields
                kind=kind,
                role=role,
                parent_uuid=obj.get("parentUuid"),
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                model=model,
                payload=raw,
                # Thread linkage
                thread_key=thread_key,
                thread_metadata=thread_metadata_json,
                thread_cwd=thread_cwd,
            )
