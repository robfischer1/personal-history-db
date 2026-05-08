"""Gemini Scribe session adapter.

Ingests vault Scribe session markdown files from Timelines/AI Sessions/*.md
(those with a session_id frontmatter field and no Gemini web url).

File structure is identical to gemini_web landmark files but uses a different
frontmatter schema:
  - session_id   — stable dedup key; used as thread_key
  - name         — human-readable session name
  - created      — YYYY-MM-DD date of the session
  - last_active  — ISO-8601 timestamp of last activity
  - enabled_tools, accessed_files, context_files — session metadata

Between ad-prompt / ad-ai-response blocks there are `> [!tools]-` callout
blocks (tool execution logs). These are skipped by the state-machine parser
inherited from gemini_web.

Thread metadata JSON:
  {"session_id": ..., "name": ..., "last_active": ...,
   "enabled_tools": [...], "accessed_files": [...]}
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.adapters.gemini_web import (
    _build_date_sent,
    _iter_blocks,
    _parse_frontmatter,
    _parse_time,
    _TITLE_TS_RE,
)


class GeminiScribeAdapter(Adapter):
    """Ingester for Gemini Scribe session markdown files."""

    name = "gemini_scribe"
    source_kind = "gemini-scribe"
    file_kind = "md"
    schema_type = "Conversation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        text = source_path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)

        session_id = fm.get("session_id", "")
        name = fm.get("name", source_path.stem)
        created = fm.get("created", "")
        last_active = fm.get("last_active", "")

        # Use session_id as thread_key; fall back to filename stem
        thread_key = session_id or source_path.stem

        # Parse list-valued frontmatter fields (YAML sequence under key)
        enabled_tools = _parse_yaml_list(text, "enabled_tools")
        accessed_files = _parse_yaml_list(text, "accessed_files")

        thread_metadata = json.dumps({
            "session_id": session_id,
            "name": name,
            "last_active": last_active,
            "enabled_tools": enabled_tools,
            "accessed_files": accessed_files[:100],
            "accessed_files_total": len(accessed_files),
        })

        for idx, (block_type, title_line, content) in enumerate(_iter_blocks(body)):
            role = "user" if block_type == "prompt" else "assistant"

            time_hhmm = None
            ts_m = _TITLE_TS_RE.search(title_line)
            if ts_m:
                time_hhmm = _parse_time(ts_m.group(1))

            date_sent = _build_date_sent(created, time_hhmm) if created else None
            body_text = content or None
            raw_hash = f"gemini-scribe:{thread_key}:{idx}"

            yield AdapterRow(
                schema_type="Conversation",
                date_sent=date_sent,
                body_text=body_text,
                is_bulk=0,
                raw_hash=raw_hash,
                kind="message",
                role=role,
                parent_uuid=None,
                tool_name=None,
                tool_use_id=None,
                model=None,
                payload=title_line + "\n" + content,
                thread_key=thread_key,
                thread_metadata=thread_metadata,
                thread_cwd=None,
            )


def _parse_yaml_list(text: str, key: str) -> list[str]:
    """Extract a simple YAML list value (sequence of quoted or bare strings)."""
    lines = text.splitlines()
    collecting = False
    results: list[str] = []
    for line in lines:
        if line.rstrip() == "---":
            if collecting:
                break
            continue
        if collecting:
            stripped = line.strip()
            if stripped.startswith("- "):
                val = stripped[2:].strip().strip('"').strip("'")
                results.append(val)
            elif stripped and not stripped.startswith("-"):
                break  # next key
        elif line.startswith(f"{key}:"):
            collecting = True
    return results
