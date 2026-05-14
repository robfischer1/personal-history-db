"""Apple Notes full-body adapter — upgrades existing note rows with decoded ZDATA.

Source: NoteStore.sqlite from a decrypted iPhone backup.
This is UPDATE-oriented: it finds existing rows (keyed as notes:{Z_PK} from
the apple_dbs adapter) and replaces truncated ZSNIPPET bodies with the full
text decoded from the ZICNOTEDATA.ZDATA gzip-compressed protobuf.

Custom run() — does not use iter_rows.
"""

from __future__ import annotations

import gzip
import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.apple_notes_full")

APPLE_EPOCH_OFFSET = 978307200


def _apple_ts_to_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    try:
        unix = float(ts) + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(unix, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    return result, pos


def _extract_field(data: bytes | None, target_field: int) -> bytes | None:
    if data is None:
        return None
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _read_varint(data, pos)
        except Exception:
            break
        field_num = tag >> 3
        wire_type = tag & 0x7

        if wire_type == 0:
            _, pos = _read_varint(data, pos)
        elif wire_type == 1:
            pos += 8
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            if field_num == target_field:
                return data[pos : pos + length]
            pos += length
        elif wire_type == 5:
            pos += 4
        else:
            break
    return None


def _decode_note_body(zdata_blob: bytes | None) -> str:
    if not zdata_blob:
        return ""
    try:
        raw = gzip.decompress(zdata_blob)
    except Exception:
        raw = zdata_blob

    data_bytes = _extract_field(raw, 2)
    if data_bytes is None:
        return ""
    inner_bytes = _extract_field(data_bytes, 3)
    if inner_bytes is None:
        return ""
    text_bytes = _extract_field(inner_bytes, 2)
    if text_bytes is None:
        return ""
    try:
        return text_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return text_bytes.decode("utf-8", errors="replace")


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

        src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row

        try:
            notes = src.execute(
                """SELECT
                       n.Z_PK AS pk,
                       COALESCE(NULLIF(n.ZTITLE1, ''), NULLIF(n.ZTITLE, ''), '') AS title,
                       n.ZSNIPPET AS snippet,
                       COALESCE(n.ZCREATIONDATE3, n.ZCREATIONDATE2,
                                n.ZCREATIONDATE1, n.ZCREATIONDATE) AS created,
                       nd.ZDATA AS zdata
                     FROM ZICCLOUDSYNCINGOBJECT n
                     LEFT JOIN ZICNOTEDATA nd ON nd.ZNOTE = n.Z_PK
                    WHERE n.Z_ENT = 12
                      AND COALESCE(n.ZMARKEDFORDELETION, 0) = 0
                    ORDER BY COALESCE(n.ZCREATIONDATE3, n.ZCREATIONDATE2,
                                      n.ZCREATIONDATE1, n.ZCREATIONDATE)"""
            ).fetchall()
        except sqlite3.OperationalError as e:
            log.warning("[%s] Could not read source: %s", self.name, e)
            src.close()
            return report

        for note in notes:
            report.rows_yielded += 1
            title = note["title"] or "Untitled"
            snippet = note["snippet"] or ""
            zdata = note["zdata"]
            created_iso = _apple_ts_to_iso(note["created"])

            full_body = _decode_note_body(bytes(zdata) if zdata else None)
            body_text = full_body if full_body.strip() else snippet
            body_source = "apple-notes-proto" if full_body.strip() else "apple-notes-snippet"

            msg_id_key = f"notes:{note['pk']}"

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
                raw_hash = hashlib.sha256(msg_id_key.encode()).hexdigest()
                self._insert_row(
                    conn,
                    AdapterRow(
                        schema_type="DigitalDocument",
                        rfc822_message_id=msg_id_key,
                        subject=title,
                        date_sent=created_iso,
                        body_text=body_text,
                        body_text_source=body_source,
                        raw_hash=raw_hash,
                        body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
                    ),
                    source_file_id,
                )
                report.rows_inserted += 1

        src.close()
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted/updated, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
