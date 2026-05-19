"""Apple Notes full-body adapter — upgrades existing note rows with decoded ZDATA.

Source: NoteStore.sqlite from a decrypted iPhone backup.
This is UPDATE-oriented: it finds existing rows (keyed as notes:{Z_PK} from
the apple_dbs adapter) and replaces truncated ZSNIPPET bodies with the full
text decoded from the ZICNOTEDATA.ZDATA gzip-compressed protobuf.

Custom run() — does not use iter_rows.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.formats.apple_notes_sqlite import parse
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.apple_notes_full")


class AppleNotesFullAdapter(Adapter):
    """Upgrade existing Apple Notes rows with full decoded body text."""

    name = "apple_notes_full"
    source_kind = "apple-notes"
    file_kind = "sqlite"
    schema_type = "DigitalDocument"
    target_table = "documents"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 100

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly — UPDATE-oriented adapter")

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

        for msg_id_key, doc in parse(source_path):
            report.rows_yielded += 1

            body_text = doc.body_text or ""
            body_source = doc.body_text_source or "apple-notes-snippet"

            tbl = self.target_table
            existing = conn.execute(
                f"SELECT id, body_text FROM {tbl} WHERE rfc822_message_id = ?",
                (msg_id_key,),
            ).fetchone()

            if existing:
                old_len = len(existing[1] or "")
                new_len = len(body_text)
                if new_len > old_len:
                    conn.execute(
                        f"UPDATE {tbl} SET body_text = ?, body_text_source = ? WHERE id = ?",
                        (body_text, body_source, existing[0]),
                    )
                    report.rows_inserted += 1
                else:
                    report.rows_skipped += 1
            else:
                raw_hash = doc.provenance.raw_hash
                self._insert_row(
                    conn,
                    AdapterRow(
                        schema_type="DigitalDocument",
                        rfc822_message_id=msg_id_key,
                        subject=doc.title,
                        date_sent=doc.created_date,
                        body_text=body_text,
                        body_text_source=body_source,
                        raw_hash=raw_hash,
                        body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
                    ),
                    source_file_id,
                )
                report.rows_inserted += 1

        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted/updated, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
