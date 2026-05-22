"""Chat logs adapter — ingests legacy IM chat logs (AIM, MSN, Trillian, Yahoo).

Source: a directory tree containing chat-log files in three formats:

  1. AIM HTML logs (.htm) — AOL Instant Messenger native export. Color-coded
     <SPAN>/<FONT> blocks per message.

  2. Plaintext session logs (.log / .txt) — Trillian-derived format used by
     MSN, Yahoo, and per-handle AIM exports. Format:
        Session Start (PROTO - my_handle:remote_handle): Mon Jul 14 14:00:00 2003
        Handle: message text
        Session Close (remote_handle): Mon Jul 14 14:13:30 2003

  3. Bracketed-time logs — ``[HH:MM] Sender: msg`` format from saved-as-text.

Per-file resume: completed relative paths are tracked in source_files.notes
as a JSON list. One source_files row covers the whole chat-logs root directory.

Thread key: ``{proto}:{my}:{remote}:{start_ts}:{path_hash}`` — one
Conversation per session.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.formats.chat_logs_text import ChatSession, discover_files, parse_file
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.chat_logs")

_LOG_EVERY_FILES = 25


class ChatLogsAdapter(Adapter):
    """Ingest legacy IM chat log directories (AIM/MSN/Trillian/Yahoo)."""

    name = "chat_logs"
    source_kind = "chat-logs"
    file_kind = "mixed"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def __init__(
        self,
        *,
        max_seconds: float | None = None,
        include_pattern: str | None = None,
    ) -> None:
        self.max_seconds = max_seconds
        self.include_pattern = re.compile(include_pattern) if include_pattern else None

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield from ()

    def compute_raw_hash(self, row: AdapterRow) -> str:
        seed = (
            f"{row.thread_key or ''}|{row.source_byte_offset or 0}"
            f"|{row.date_sent or ''}|{row.sender_address or ''}"
            f"|{(row.body_text or '')[:100]}"
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

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

    def _iter_session_rows(
        self,
        session: ChatSession,
        file_relpath: str,
        session_index: int,
        file_index: int,
        owner_names: set[str],
    ) -> Iterator[AdapterRow]:
        proto = session.protocol or "unknown"
        my = session.my_handle or "unknown"
        remote = session.remote_handle or "unknown"
        start = session.start_ts or session.session_date or "unknown"
        path_hash = hashlib.sha256(f"{file_relpath}#sess{session_index}".encode()).hexdigest()[:8]
        thread_key = f"{proto}:{my}:{remote}:{start}:{path_hash}"

        for msg in session.messages:
            body = msg.body_text or ""
            if not body:
                continue

            sender_name = msg.sender_name or ""
            sender_addr = msg.sender_address or ""

            if self._is_owner(sender_name, owner_names) or (sender_addr and sender_addr == my):
                direction = "outbound"
            elif sender_addr and sender_addr == remote:
                direction = "inbound"
            elif self._is_owner(sender_addr, owner_names):
                direction = "outbound"
            else:
                direction = "inbound"

            raw_hash = msg.provenance.raw_hash
            body_hash = hashlib.sha256(body.encode()).hexdigest()

            sender_domain: str | None = None
            if sender_addr and "@" in sender_addr:
                sender_domain = sender_addr.split("@", 1)[1]

            recipients: list[dict[str, str]] = []
            other = remote if direction == "outbound" else my
            if other and other != "unknown":
                recipients.append({"address": other, "name": "", "rtype": "to"})

            yield AdapterRow(
                schema_type="Message",
                rfc822_message_id=msg.platform_id or f"chatlog:{raw_hash}",
                sender_address=sender_addr or None,
                sender_name=sender_name or None,
                sender_domain=sender_domain,
                direction=direction,
                date_sent=msg.date_sent or None,
                body_text=body,
                body_text_source="chat-log",
                is_multipart=0,
                has_attachments=0,
                attachment_count=0,
                is_bulk=0,
                source_byte_offset=file_index,
                source_byte_length=msg.provenance.source_byte_length,
                raw_hash=raw_hash,
                body_text_hash=body_hash,
                recipients=recipients,
                thread_key=thread_key,
            )

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id
        log.info("[%s] Source registered: id=%d path=%s", self.name, source_file_id, source_path)

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
            self.name, len(all_files), len(done_files), len(todo),
        )

        t_start = time.time()
        files_done = 0
        touched_threads: set[int] = set()
        thread_dates: dict[int, tuple[str, str]] = {}

        for fi, file_path in enumerate(todo):
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[%s] Time budget reached after %d files", self.name, files_done)
                break

            relpath = str(file_path.relative_to(source_path))
            try:
                sessions = parse_file(file_path, source_path)
                for sidx, session in enumerate(sessions):
                    for row in self._iter_session_rows(
                        session, relpath, sidx, fi, all_owner_ids,
                    ):
                        report.rows_yielded += 1

                        if row.body_text and not row.body_text_hash:
                            row.body_text_hash = hashlib.sha256(
                                row.body_text.encode("utf-8")
                            ).hexdigest()

                        message_id = self._insert_row(conn, row, source_file_id)
                        if message_id is None:
                            report.rows_skipped += 1
                            continue

                        report.rows_inserted += 1
                        self._insert_sidecars(conn, message_id, row)

                        if row.thread_key:
                            my_h = session.my_handle or "unknown"
                            remote_h = session.remote_handle or "unknown"
                            participants = sorted({h for h in [my_h, remote_h] if h})
                            thread_id, created = self._upsert_thread(
                                conn, row.thread_key, participants
                            )
                            self._link_message_thread(conn, message_id, thread_id)
                            if created:
                                report.threads_created += 1
                            touched_threads.add(thread_id)
                            rd = row.date_sent
                            if rd and thread_id in thread_dates:
                                lo, hi = thread_dates[thread_id]
                                thread_dates[thread_id] = (min(lo, rd), max(hi, rd))
                            elif rd:
                                thread_dates[thread_id] = (rd, rd)

                self._mark_file_done(conn, source_file_id, relpath)
                conn.commit()
                files_done += 1

            except Exception:
                log.exception("[%s] Error processing %s", self.name, relpath)
                report.errors.append(relpath)

            if files_done % _LOG_EVERY_FILES == 0 and files_done > 0:
                log.info(
                    "[%s] Progress: %d/%d files, %d rows inserted",
                    self.name, files_done, len(todo), report.rows_inserted,
                )

        for tid in touched_threads:
            dates = thread_dates.get(tid)
            self._update_thread_aggregates(
                conn, tid,
                dates[0] if dates else None,
                dates[1] if dates else None,
            )

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d files, %d yielded, %d inserted, %d skipped, %d threads",
            self.name, files_done, report.rows_yielded, report.rows_inserted,
            report.rows_skipped, report.threads_created,
        )
        return report
