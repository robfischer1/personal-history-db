"""Claude Code plugin — ingests Claude Code JSONL session files."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.claude_code_jsonl import parse as parse_claude_code
from phdb.log import get_logger
from phdb.plugins.claude_code.ingest import emit_thread_triple, upsert_message

if TYPE_CHECKING:
    from phdb.records import AISessionMessage
    from phdb.settings import Settings

log = get_logger("phdb.plugins.claude_code")

# Matches either a canonical session UUID (8-4-4-4-12 hex) or an agent
# sub-session identifier (`agent-<hex>`), anchored to the `.jsonl` tail.
# Both are unique-per-session/agent and feed source_files.session_uuid
# so the partial UNIQUE index on (source_kind, session_uuid) protects
# against duplicate ingests when a session file is later relocated.
_UUID_TAIL_RE = re.compile(
    r"("
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|agent-[0-9a-f]+"
    r")\.jsonl$",
    re.IGNORECASE,
)

_HOME_CLAUDE_DIR = Path.home() / ".claude"


@dataclass
class IngestSummary:
    """Result of one ``run()`` call."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)


def _register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "claude-code",
    file_kind: str = "jsonl",
) -> int:
    """Insert or refresh a source_files row."""
    m = _UUID_TAIL_RE.search(source_path.name)
    session_uuid = m.group(1).lower() if m else None

    cur = conn.execute(
        """INSERT INTO source_files
           (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
           VALUES (?, ?, ?, ?, ?,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           ON CONFLICT(source_path) DO UPDATE
             SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                 session_uuid = COALESCE(source_files.session_uuid, excluded.session_uuid)
           ON CONFLICT(source_kind, session_uuid) WHERE session_uuid IS NOT NULL
             DO UPDATE SET source_path = excluded.source_path,
                           ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           RETURNING id""",
        (str(source_path), None, file_kind, source_kind, session_uuid),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


class ClaudeCodePlugin(PhdbSourcePlugin):
    """Claude Code plugin — Phase 7 port."""

    SOURCE_KIND = "claude-code"
    FILE_KIND = "jsonl"
    BATCH_SIZE = 500

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every JSONL."""
        if root.is_file():
            if root.suffix.lower() == ".jsonl":
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.jsonl")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[AISessionMessage]:
        """Yield AISessionMessage records from one JSONL file."""
        yield from parse_claude_code(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: AISessionMessage,
        *,
        source_file_id: int,
    ) -> int | None:
        """Ingest a single conversation message."""
        message_id = upsert_message(conn, source_file_id, record)
        if message_id is None:
            return None

        if record.thread_key:
            emit_thread_triple(conn, self.SOURCE_KIND, message_id, record.thread_key)

        return message_id

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Claude Code JSONL file."""
        self._validate_source_path(source_path)

        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        first_rec = True

        for record in self.parse(source_path):
            report.rows_yielded += 1

            if first_rec and record.thread_key:
                # Thread-node creation signal (graph layer; the legacy
                # `threads` table was dropped in the Messages Decomposition
                # refactor — migrations 0016-0022).
                label = f"{self.SOURCE_KIND}:{record.thread_key}"
                exists = conn.execute(
                    "SELECT 1 FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
                    (label.lower(),),
                ).fetchone()
                if not exists:
                    report.threads_created += 1
                first_rec = False

            msg_id = self.ingest_row(conn, record, source_file_id=source_file_id)
            if msg_id:
                report.rows_inserted += 1
            else:
                report.rows_skipped += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[claude_code] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report

    # --------------------------- Private helpers ---------------------------

    def _validate_source_path(self, source_path: Path) -> None:
        """Refuse live .claude path."""
        try:
            source_path.resolve().relative_to(_HOME_CLAUDE_DIR.resolve())
            raise ValueError(
                f"claude_code adapter refuses live .claude path {source_path!r}; "
                f"ingest from the canonical AI-sessions archive location instead"
            )
        except ValueError as exc:
            if "refuses live" in str(exc):
                raise

