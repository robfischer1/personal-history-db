"""MsnPlusPlugin — ingests MSN Messenger Plus! chat logs with bar-framed
session headers and [HH:MM:SS AM/PM] timestamps.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.chat_logs_text import (
    ChatSession,
    detect_format,
    discover_files,
    parse_file,
)
from phdb.formats.chat_upserts import (
    emit_chat_recipient_triples,
    emit_chat_thread_triple,
    upsert_chat_message,
)
from phdb.log import get_logger
from phdb.records.common import Recipient

from dataclasses import replace

from phdb.records import ChatMessage

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.msn_plus")

_LOG_EVERY_FILES = 25


class MsnPlusPlugin(PhdbSourcePlugin):
    """MSN Messenger Plus! chat log ingester."""

    SOURCE_KIND = "msn-plus"
    FILE_KIND = "txt"
    BATCH_SIZE = 500

    def __init__(
        self,
        manifest: PluginManifest,
        *,
        max_seconds: float | None = None,
    ) -> None:
        super().__init__(manifest)
        self.max_seconds = max_seconds

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        for path in discover_files(root, include_pattern=None):
            try:
                head = path.read_bytes()[:8192]
            except (OSError, PermissionError):
                continue
            if detect_format(path, head) == "msn_plus":
                yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[tuple[str, ChatMessage]]:
        sessions = parse_file(path, path.parent)
        for session in sessions:
            for msg in session.messages:
                yield "unknown", msg

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage,
        *,
        source_file_id: int,
        direction: str = "unknown",
        thread_key: str | None = None,
    ) -> int | None:
        msg_id = upsert_chat_message(
            conn, source_file_id, record,
            direction=direction,
            body_text_source="msn-plus-log",
        )
        if msg_id is None:
            return None

        emit_chat_recipient_triples(conn, "msn", msg_id, record)

        t_key = thread_key or record.thread_key
        if t_key:
            emit_chat_thread_triple(conn, "msn", msg_id, t_key)

        return msg_id

    def project_facets(self, emission_bus: Any, record: ChatMessage) -> None:
        return None

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    @staticmethod
    def _is_owner(handle: str | None, owner_ids: set[str]) -> bool:
        if not handle:
            return False
        return handle.strip().lower() in owner_ids

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> Any:
        from phdb.core.plugin.summary import IngestSummary

        report = IngestSummary(source_path=str(source_path))

        msn_handles = settings.identity.owner_handles.get("msn", set())
        owner_ids: set[str] = {h.lower() for h in msn_handles}
        owner_ids.update(n.lower() for n in settings.identity.owner_names)
        for email in settings.identity.owner_emails:
            owner_ids.add(email.lower())
            local = email.split("@", 1)[0] if "@" in email else email
            owner_ids.add(local.lower())

        all_files: list[Path] = []
        for path in discover_files(source_path, include_pattern=None):
            try:
                head = path.read_bytes()[:8192]
            except (OSError, PermissionError):
                continue
            if detect_format(path, head) == "msn_plus":
                all_files.append(path)

        log.info("[msn_plus] Found %d MSN Plus! files under %s", len(all_files), source_path)

        t_start = time.time()
        files_done = 0

        for file_path in all_files:
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[msn_plus] Time budget reached after %d files", files_done)
                break

            source_file_id = _register_source_file(
                conn, file_path,
                source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
            )
            report.source_file_id = source_file_id

            try:
                sessions = parse_file(file_path, source_path)
            except Exception:
                log.exception("[msn_plus] Error parsing %s", file_path)
                continue

            for sidx, session in enumerate(sessions):
                proto = session.protocol or "msn"
                my = session.my_handle or "unknown"
                remote = session.remote_handle or "unknown"
                start = session.start_ts or session.session_date or "unknown"
                relpath = str(file_path.relative_to(source_path))
                path_hash = hashlib.sha256(
                    f"{relpath}#sess{sidx}".encode()
                ).hexdigest()[:8]
                thread_key = f"{proto}:{my}:{remote}:{start}:{path_hash}"

                for msg in session.messages:
                    report.rows_yielded += 1

                    sender_addr = msg.sender_address or ""
                    sender_name = msg.sender_name or ""

                    if sender_addr and sender_addr == my:
                        direction = "outbound"
                    elif sender_addr and sender_addr == remote:
                        direction = "inbound"
                    elif self._is_owner(sender_addr, owner_ids):
                        direction = "outbound"
                    elif self._is_owner(sender_name, owner_ids):
                        direction = "outbound"
                    else:
                        direction = "inbound"

                    other = remote if direction == "outbound" else my
                    if other and other != "unknown":
                        msg = replace(msg, recipients=(Recipient(address=other),))

                    msg_id = self.ingest_row(
                        conn, msg,
                        source_file_id=source_file_id,
                        direction=direction,
                        thread_key=thread_key,
                    )
                    if msg_id is not None:
                        report.rows_inserted += 1
                    else:
                        report.rows_skipped += 1

            conn.commit()
            files_done += 1

            if files_done % _LOG_EVERY_FILES == 0 and files_done > 0:
                log.info(
                    "[msn_plus] Progress: %d/%d files, %d rows inserted",
                    files_done, len(all_files), report.rows_inserted,
                )

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, report.source_file_id),
        )
        conn.commit()

        log.info(
            "[msn_plus] Done: %d files, %d yielded, %d inserted, %d skipped",
            files_done, report.rows_yielded, report.rows_inserted,
            report.rows_skipped,
        )
        return report
