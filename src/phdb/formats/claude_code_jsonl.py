"""Claude Code JSONL format parser — yields AISessionMessage records.

Parses ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl files.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from phdb.records import AISessionMessage, Provenance

_TURN_TYPES: frozenset[str] = frozenset({"user", "assistant"})
_META_TYPES: frozenset[str] = frozenset({"user", "assistant", "attachment"})

_UUID_TAIL_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)


def _extract_meta(obj: dict) -> dict[str, str | None]:
    return {
        "sessionId": obj.get("sessionId"),
        "cwd": obj.get("cwd"),
        "version": obj.get("version"),
        "gitBranch": obj.get("gitBranch"),
        "userType": obj.get("userType"),
        "entrypoint": obj.get("entrypoint"),
    }


def _body_text_from_content(content: list | str | None) -> str | None:
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

    if line_type == "assistant":
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                return "tool_use", item.get("name"), item.get("id")
        if is_sidechain or all(t in ("thinking", None) for t in content_types):
            return "sidechain", None, None
        return "message", None, None

    if line_type == "user":
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                return "tool_result", None, item.get("tool_use_id")
        return "message", None, None

    return "unknown", None, None


def parse(source_path: Path) -> Iterator[AISessionMessage]:
    """Parse a Claude Code JSONL session file, yielding AISessionMessage records."""
    source_str = str(source_path)
    session_uuid = source_path.stem
    thread_key = session_uuid

    with source_path.open(encoding="utf-8") as fh:
        raw_lines = fh.readlines()

    thread_meta: dict[str, str | None] = {}
    thread_cwd: str | None = None

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

        role: str = "user" if line_type == "user" else "assistant"
        body_text = _body_text_from_content(content)
        model: str | None = msg.get("model") if line_type == "assistant" else None

        yield AISessionMessage(
            provenance=Provenance(source_path=source_str, raw_hash=f"claude-code:{turn_uuid}"),
            date_sent=obj.get("timestamp") or "",
            kind=kind,
            role=role,
            thread_key=thread_key,
            body_text=body_text,
            model=model,
            parent_uuid=obj.get("parentUuid"),
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            payload=raw,
            thread_metadata=thread_meta if thread_meta else None,
        )
