"""iMessage plugin — port of the legacy imessage adapter.

Phase 7 of the phdb Plugin Architecture plan.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.imessage_html import (
    discover_html_files,
    is_bulk_sender,
    normalize_addr,
    parse_file,
    parse_filename_participants,
)
from phdb.log import get_logger
from phdb.records import ChatMessage
from phdb.triples import resolve_node, get_predicate

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.imessage")

_MAX_BODY_LEN = 50_000

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
    source_kind: str = "imessage",
    file_kind: str = "html",
) -> int:
    """Insert (or refresh) a source_files row for the given path."""
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

class IMessagePlugin(PhdbSourcePlugin):
    """iMessage HTML export plugin."""

    SOURCE_KIND = "imessage"
    FILE_KIND = "html"
    BATCH_SIZE = 500

    def __init__(self, manifest: PluginManifest, *, max_seconds: float | None = None) -> None:
        super().__init__(manifest)
        self.max_seconds = max_seconds
        self._name_to_phone: dict[str, str] = {}
        self._predicate_cache: dict[str, int] = {}

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every iMessage HTML."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        one_on_one, groups = discover_html_files(root)
        for f in one_on_one + groups:
            yield f, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ChatMessage]:
        """Yield ChatMessage records from one iMessage HTML file."""
        yield from parse_file(
            path,
            name_to_phone=self._name_to_phone,
        )

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage,
        *,
        source_file_id: int | None = None,
        direction: str = "unknown",
    ) -> int | None:
        """Insert ChatMessage + sidecars (attachments, recipients)."""
        sf_id = source_file_id if source_file_id is not None else 0
        
        body = record.body_text
        if body and len(body) > _MAX_BODY_LEN:
            body = body[:_MAX_BODY_LEN]
        body_hash = hashlib.sha256(body.encode()).hexdigest() if body else None

        sender_domain: str | None = None
        if record.sender_address and "@" in record.sender_address:
            sender_domain = record.sender_address.split("@", 1)[1]

        bulk_flag, bulk_sig = is_bulk_sender(record.sender_address) if record.sender_address else (False, None)

        cur = conn.execute(
            """INSERT OR IGNORE INTO chat_messages (
                schema_type, message_key, subject, sender_address, sender_name, sender_domain,
                direction, date_sent, body_text, body_text_source, body_text_hash,
                is_multipart, has_attachments, attachment_count, is_bulk, bulk_signal,
                source_byte_offset, source_byte_length, raw_hash, source_file_id
            ) VALUES ('Message', ?, NULL, ?, ?, ?, ?, ?, ?, 'imessage-html', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id""",
            (
                record.platform_id, record.sender_address, record.sender_name, sender_domain,
                direction, record.date_sent, body, body_hash,
                int(record.is_multipart), int(record.has_attachments), record.attachment_count,
                int(bulk_flag), bulk_sig,
                record.provenance.source_byte_offset, record.provenance.source_byte_length,
                record.provenance.raw_hash, sf_id
            )
        )
        row = cur.fetchone()
        if not row:
            return None
        
        message_id = int(row[0])
        self._insert_sidecars(conn, message_id, record)
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
        settings: Settings,
        max_seconds: float | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of iMessage HTML directory."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        owner_phones = settings.identity.owner_phones
        owner_names = settings.identity.owner_names
        owner_phone = next(iter(owner_phones), None)

        one_on_one, groups = discover_html_files(source_path)
        ordered = one_on_one + groups

        done_files = self._get_done_files(conn, source_file_id)
        todo = [f for f in ordered if f.name not in done_files]
        log.info(
            "[imessage] Files: %d total (%d 1-on-1, %d group), %d done, %d remaining",
            len(ordered), len(one_on_one), len(groups),
            len(done_files), len(todo),
        )

        self._name_to_phone = {}
        self._rebuild_name_lookup(conn, source_file_id)

        t_start = time.time()
        files_done = 0
        batch_count = 0
        budget = max_seconds if max_seconds is not None else self.max_seconds

        for html_file in todo:
            if budget and (time.time() - t_start) > budget:
                log.info("[imessage] Time budget reached after %d files", files_done)
                break

            try:
                for record in parse_file(
                    html_file,
                    owner_phone=owner_phone,
                    name_to_phone=self._name_to_phone,
                    owner_names=owner_names,
                ):
                    report.rows_yielded += 1

                    if (
                        record.sender_address
                        and owner_phone
                        and normalize_addr(record.sender_address) == normalize_addr(owner_phone)
                    ):
                        direction = "outbound"
                    elif record.sender_address:
                        direction = "inbound"
                    else:
                        direction = "unknown"

                    message_id = self.ingest_row(
                        conn, record, source_file_id=source_file_id, direction=direction
                    )
                    if message_id is None:
                        report.rows_skipped += 1
                        continue

                    report.rows_inserted += 1

                    if record.thread_key:
                        thread_id, created = self._upsert_thread(conn, record.thread_key)
                        self._link_message_thread(conn, message_id, thread_id)
                        if created:
                            report.threads_created += 1

                    batch_count += 1
                    if batch_count >= self.BATCH_SIZE:
                        conn.commit()
                        batch_count = 0

                self._mark_file_done(conn, source_file_id, html_file.name)
                conn.commit()
                files_done += 1

            except Exception:
                log.exception("[imessage] Error processing %s", html_file.name)
                report.errors.append(html_file.name)

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[imessage] Done: %d files, %d yielded, %d inserted, %d skipped, %d threads",
            files_done, report.rows_yielded, report.rows_inserted, report.rows_skipped,
            report.threads_created,
        )
        return report

    # --------------------------- Private helpers ---------------------------

    def _resolve_predicate_id(self, conn: sqlite3.Connection, name: str) -> int:
        if name in self._predicate_cache:
            return self._predicate_cache[name]
        pred = get_predicate(conn, name)
        if pred is None:
            raise ValueError(f"Predicate {name!r} not found")
        self._predicate_cache[name] = pred["id"]
        return pred["id"]

    def _insert_sidecars(
        self, conn: sqlite3.Connection, message_id: int, record: ChatMessage
    ) -> None:
        self._emit_recipient_triples(conn, message_id, record)
        for a in record.attachments:
            conn.execute(
                """INSERT INTO attachments (schema_type, message_id, filename, content_type,
                   content_disposition, size_bytes, on_disk_path, content_hash)
                   VALUES ('DigitalDocument', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id, a.filename, a.content_type, a.content_disposition,
                    a.size_bytes, a.on_disk_path, a.content_hash,
                ),
            )

    def _emit_recipient_triples(
        self, conn: sqlite3.Connection, row_id: int, record: ChatMessage
    ) -> None:
        if not record.recipients:
            return

        sent_to_id = self._resolve_predicate_id(conn, "sentTo")
        record_label = f"chat_messages:{row_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table="chat_messages", source_id=row_id,
        )

        for r in record.recipients:
            address = r.address
            if not address:
                continue

            contact_node_id = resolve_node(
                conn, address.lower(), "contact",
                source_table="chat_messages", source_id=row_id,
            )

            conn.execute(
                "INSERT OR IGNORE INTO triples"
                " (subject_node_id, predicate_id, object_node_id,"
                "  provenance, source_ref)"
                " VALUES (?, ?, ?, 'plugin', ?)",
                (record_node_id, sent_to_id, contact_node_id, self.SOURCE_KIND),
            )

    def _upsert_thread(self, conn: sqlite3.Connection, thread_key: str) -> tuple[int, bool]:
        label = f"{self.SOURCE_KIND}:{thread_key}"
        existing = conn.execute(
            "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
            (label.lower(),),
        ).fetchone()
        if existing:
            return existing[0], False

        node_id = resolve_node(conn, label, "thread")
        return node_id, True

    def _link_message_thread(
        self, conn: sqlite3.Connection, message_id: int, thread_node_id: int
    ) -> None:
        in_thread_id = self._resolve_predicate_id(conn, "inThread")
        record_label = f"chat_messages:{message_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table="chat_messages", source_id=message_id,
        )

        conn.execute(
            "INSERT OR IGNORE INTO triples"
            " (subject_node_id, predicate_id, object_node_id,"
            "  provenance, source_ref)"
            " VALUES (?, ?, ?, 'plugin', ?)",
            (record_node_id, in_thread_id, thread_node_id, self.SOURCE_KIND),
        )

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
        self, conn: sqlite3.Connection, source_file_id: int, filename: str
    ) -> None:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        done = set(json.loads(row[0])) if row and row[0] else set()
        done.add(filename)
        conn.execute(
            "UPDATE source_files SET notes = ? WHERE id = ?",
            (json.dumps(sorted(done)), source_file_id),
        )

    def _rebuild_name_lookup(
        self, conn: sqlite3.Connection, source_file_id: int
    ) -> None:
        for r in conn.execute(
            """SELECT sender_name, sender_address FROM chat_messages
               WHERE sender_address IS NOT NULL AND sender_name IS NOT NULL
                 AND sender_address LIKE '+%' AND source_file_id = ?
               GROUP BY sender_name""",
            (source_file_id,),
        ):
            self._name_to_phone[r[0]] = r[1]
