"""Clippings ingest helpers — clippings-table upsert.

Lifted from the legacy ``phdb.adapters.clippings`` + the
``_insert_clipping`` path in ``phdb.adapters.base`` so the plugin
doesn't need to inherit the deprecated ``Adapter`` base class. Mirrors
the apple_notes_full / goodreads plugin ingest shapes.
"""

from __future__ import annotations

import hashlib
import sqlite3

from phdb.formats.clippings_md import ClippingRecord

_INSERT_CLIPPING_SQL = """\
INSERT OR IGNORE INTO clippings (
    schema_type, subject, url, publisher, creator, description, image_url,
    categories, tags, aliases, note_type, author_type,
    file_path, file_size, ctime, mtime,
    body_text, body_text_source, body_text_hash,
    raw_hash, bucket, source_file_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def upsert_clipping(
    conn: sqlite3.Connection,
    source_file_id: int,
    rec: ClippingRecord,
) -> int | None:
    """Insert one ClippingRecord into the ``clippings`` table.

    Returns the inserted row id, or ``None`` when the row was a
    dedup-skip (UNIQUE(source_file_id, raw_hash)).
    """
    body_text = rec.body_text or ""
    body_text_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

    cur = conn.execute(
        _INSERT_CLIPPING_SQL,
        (
            rec.schema_type,
            rec.title,                      # subject
            rec.url,
            rec.publisher,
            rec.creator,
            rec.description,
            rec.image_url,
            rec.categories,
            rec.tags,
            rec.aliases,
            rec.note_type,
            rec.author_type,
            rec.file_path,
            rec.file_size,
            rec.ctime,
            rec.mtime,
            body_text,
            rec.body_text_source,
            body_text_hash,
            rec.provenance.raw_hash,
            rec.bucket,
            source_file_id,
        ),
    )
    if cur.rowcount == 0:
        return None
    return int(cur.lastrowid)  # type: ignore[arg-type]


__all__ = ["upsert_clipping"]
