"""ChatLogsPlugin — ingests legacy IM chat logs (AIM, MSN, Trillian, Yahoo).

Ported from legacy ChatLogsAdapter.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.chat_logs_text import ChatSession, discover_files, parse_file
from phdb.formats.chat_upserts import (
    emit_chat_recipient_triples,
    emit_chat_thread_triple,
    upsert_chat_message,
)
from phdb.log import get_logger
from phdb.records import ChatMessage
from phdb.records.common import Recipient

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.chat_logs")

_LOG_EVERY_FILES = 25


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

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
    source_kind: str = "chat-logs",
    file_kind: str = "mixed",
) -> int:
    """Insert or refresh a source_files row for the given path."""
    cur = conn.execute(
        """INSERT INTO source_files
           (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
           VALUES (?, ?, ?, ?, NULL,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           ON CONFLICT(source_path) DO UPDATE
             SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           RETURNING id""",
        (str(source_path), None, file_kind, source_kind),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


class ChatLogsPlugin(PhdbSourcePlugin):
    """Legacy IM chat log plugin."""

    SOURCE_KIND = "chat-logs"
    FILE_KIND = "mixed"
    BATCH_SIZE = 500

    def __init__(
        self,
        manifest: PluginManifest,
        *,
        max_seconds: float | None = None,
        include_pattern: str | None = None,
    ) -> None:
        super().__init__(manifest)
        self.max_seconds = max_seconds
        self.include_pattern_str = include_pattern
        self.include_pattern = re.compile(include_pattern) if include_pattern else None

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Yield chat log files found under root."""
        for path in discover_files(root, self.include_pattern):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[tuple[ChatSession, str]]:
        """Yield (ChatSession, relpath) pairs.

        Note: this plugin's parse method yields tuples because ChatSession
        needs context (relpath) to generate thread_keys correctly.
        """
        # We need a root to get a relpath.
        # For ChatLogs, the 'path' passed to parse is an individual file,
        # but the thread_key needs its relpath from the original ingest root.
        # This plugin expects run() to handle this.
        # If called via generic parse(path), relpath becomes just the filename.
        sessions = parse_file(path, path.parent)
        for session in sessions:
            yield session, path.name

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage,
        *,
        source_file_id: int | None = None,
        direction: str = "unknown",
        thread_key: str | None = None,
    ) -> int | None:
        """Upsert a ChatMessage + triples."""
        sf_id = source_file_id if source_file_id is not None else 0

        msg_id = upsert_chat_message(
            conn, sf_id, record,
            direction=direction,
            body_text_source="chat-log",
        )

        if msg_id:
            emit_chat_recipient_triples(conn, self.SOURCE_KIND, msg_id, record)
            t_key = thread_key or record.thread_key
            if t_key:
                emit_chat_thread_triple(conn, self.SOURCE_KIND, msg_id, t_key)

        return msg_id

    def register_cli(self, parser: Any) -> None:
        """Register CLI subcommands."""
        return None

    def register_tools(self, server: Any) -> None:
        """Register MCP tools."""
        return None

    # --------------------------- Private helpers ---------------------------

    def _get_done_files(self, conn: sqlite3.Connection, source_file_id: int) -> set[str]:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        if not row or not row[0]:
            return set()
        try:
            return set(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            return set()

    def _mark_file_done(
        self, conn: sqlite3.Connection, source_file_id: int, relpath: str
    ) -> None:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        done = set(json.loads(row[0])) if row and row[0] else set()
        done.add(relpath)
        conn.execute(
            "UPDATE source_files SET notes = ? WHERE id = ?",
            (json.dumps(sorted(done)), source_file_id),
        )

    def _is_owner(self, handle: str | None, owner_names: set[str]) -> bool:
        if not handle:
            return False
        return handle.strip().lower() in owner_names

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestSummary:
        """End-to-end ingest of a chat logs directory."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        # Identity setup
        owner_names = settings.identity.owner_names
        all_owner_ids = set(owner_names)
        for handles in settings.identity.owner_handles.values():
            all_owner_ids.update(handles)
        for email in settings.identity.owner_emails:
            all_owner_ids.add(email)
            local = email.split("@", 1)[0] if "@" in email else email
            all_owner_ids.add(local)

        all_files = discover_files(source_path, self.include_pattern)
        done_files = self._get_done_files(conn, source_file_id)
        todo = [f for f in all_files if str(f.relative_to(source_path)) not in done_files]

        log.info(
            "[%s] Files: %d discovered, %d done, %d remaining",
            self.SOURCE_KIND, len(all_files), len(done_files), len(todo),
        )

        t_start = time.time()
        files_done = 0

        for _fi, file_path in enumerate(todo):
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[%s] Time budget reached after %d files", self.SOURCE_KIND, files_done)
                break

            relpath = str(file_path.relative_to(source_path))
            try:
                sessions = parse_file(file_path, source_path)
                for sidx, session in enumerate(sessions):
                    # Replicate thread_key logic
                    proto = session.protocol or "unknown"
                    my = session.my_handle or "unknown"
                    remote = session.remote_handle or "unknown"
                    start = session.start_ts or session.session_date or "unknown"
                    path_hash = hashlib.sha256(
                        f"{relpath}#sess{sidx}".encode()
                    ).hexdigest()[:8]
                    thread_key = f"{proto}:{my}:{remote}:{start}:{path_hash}"

                    for msg in session.messages:
                        report.rows_yielded += 1

                        # Infer direction
                        sender_name = msg.sender_name or ""
                        sender_addr = msg.sender_address or ""

                        if self._is_owner(sender_name, all_owner_ids) or (
                            sender_addr and sender_addr == my
                        ):
                            direction = "outbound"
                        elif sender_addr and sender_addr == remote:
                            direction = "inbound"
                        elif self._is_owner(sender_addr, all_owner_ids):
                            direction = "outbound"
                        else:
                            direction = "inbound"

                        # Add recipients
                        other = remote if direction == "outbound" else my
                        if other and other != "unknown":
                            msg = replace(msg, recipients=(Recipient(address=other),))

                        msg_id = self.ingest_row(
                            conn, msg,
                            source_file_id=source_file_id,
                            direction=direction,
                            thread_key=thread_key,
                        )
                        if msg_id:
                            report.rows_inserted += 1
                        else:
                            report.rows_skipped += 1

                self._mark_file_done(conn, source_file_id, relpath)
                conn.commit()
                files_done += 1

            except Exception:
                log.exception("[%s] Error processing %s", self.SOURCE_KIND, relpath)
                report.errors.append(relpath)

            if files_done % _LOG_EVERY_FILES == 0 and files_done > 0:
                log.info(
                    "[%s] Progress: %d/%d files, %d rows inserted",
                    self.SOURCE_KIND, files_done, len(todo), report.rows_inserted,
                )

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d files, %d yielded, %d inserted, %d skipped",
            self.SOURCE_KIND, files_done, report.rows_yielded,
            report.rows_inserted, report.rows_skipped,
        )
        return report
