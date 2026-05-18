"""Claude Code JSONL session adapter.

Consumes AISessionMessage records from phdb.formats.claude_code_jsonl.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.claude_code_jsonl import (
    _body_text_from_content,  # noqa: F401
    _derive_kind,  # noqa: F401
    parse,
)

_UUID_TAIL_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)

_LEGACY_PATH_PREFIXES: tuple[str, ...] = (
    r"C:\Users\<owner>\.claude",
    r"c:\users\<owner>\.claude",
)


class ClaudeCodeAdapter(Adapter):
    """Ingester for Claude Code JSONL session files."""

    name = "claude_code"
    source_kind = "claude-code"
    file_kind = "jsonl"
    schema_type = "Conversation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC

    def compute_session_uuid(self, source_path: Path) -> str | None:
        m = _UUID_TAIL_RE.search(source_path.name)
        return m.group(1).lower() if m else None

    def validate_source_path(self, source_path: Path) -> None:
        p = str(source_path)
        for prefix in _LEGACY_PATH_PREFIXES:
            if p.startswith(prefix):
                raise ValueError(
                    f"claude_code adapter refuses legacy path {p!r}; "
                    f"canonical AI-sessions location is "
                    f"D:\\Records\\AI Sessions\\Claude\\ "
                    f"(see migration 0010 / project_personal_history_db memory)"
                )

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        import json

        first_rec = True
        thread_metadata_json: str | None = None
        thread_cwd: str | None = None

        for rec in parse(source_path):
            if first_rec and rec.thread_metadata:
                thread_metadata_json = json.dumps(rec.thread_metadata)
                thread_cwd = rec.thread_metadata.get("cwd")
                first_rec = False

            yield AdapterRow(
                schema_type="Conversation",
                date_sent=rec.date_sent or None,
                body_text=rec.body_text,
                is_bulk=0 if rec.kind == "message" else 1,
                raw_hash=rec.provenance.raw_hash,
                kind=rec.kind,
                role=rec.role,
                parent_uuid=rec.parent_uuid,
                tool_name=rec.tool_name,
                tool_use_id=rec.tool_use_id,
                model=rec.model,
                payload=rec.payload,
                thread_key=rec.thread_key,
                thread_metadata=thread_metadata_json,
                thread_cwd=thread_cwd,
            )
