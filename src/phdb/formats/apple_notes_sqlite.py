"""Apple Notes SQLite format parser — yields DigitalDocument records from NoteStore.sqlite.

Source: NoteStore.sqlite from a decrypted iPhone backup.
Reads ZICCLOUDSYNCINGOBJECT + ZICNOTEDATA, decodes the gzip-compressed
protobuf ZDATA blob to extract full note body text.

Proto extraction path: gunzip -> field 2 (Document) -> field 3 (Note) -> field 2 (NoteText).

Pure parser: no DB (destination), no identity.
"""

from __future__ import annotations

import gzip
import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.records import DigitalDocument, Provenance

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


def decode_note_body(zdata_blob: bytes | None) -> str:
    """Decode ZICNOTEDATA.ZDATA blob into plain text.

    Proto path: gunzip -> field 2 (Document) -> field 3 (Note) -> field 2 (NoteText).
    """
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


def parse(source_path: Path) -> Iterator[tuple[str, DigitalDocument]]:
    """Parse NoteStore.sqlite, yielding (synthetic_id, DigitalDocument) tuples.

    Each tuple pairs the synthetic message ID key (``notes:{Z_PK}``) with a
    DigitalDocument record carrying the full decoded body text (or snippet
    fallback when ZDATA decoding produces no text).

    The synthetic ID is the same key used by the apple_dbs adapter for dedup,
    so the caller can use it for UPDATE-vs-INSERT logic.
    """
    source_str = str(source_path)
    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    try:
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
        except sqlite3.OperationalError:
            return

        for note in notes:
            title = note["title"] or "Untitled"
            snippet = note["snippet"] or ""
            zdata = note["zdata"]
            created_iso = _apple_ts_to_iso(note["created"])

            full_body = decode_note_body(bytes(zdata) if zdata else None)
            body_text = full_body if full_body.strip() else snippet
            body_source = "apple-notes-proto" if full_body.strip() else "apple-notes-snippet"

            msg_id_key = f"notes:{note['pk']}"
            raw_hash = hashlib.sha256(msg_id_key.encode()).hexdigest()

            yield msg_id_key, DigitalDocument(
                provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                title=title,
                body_text=body_text,
                body_text_source=body_source,
                created_date=created_iso,
                document_type="apple-note",
            )
    finally:
        src.close()
