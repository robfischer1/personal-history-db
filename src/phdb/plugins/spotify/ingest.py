"""Spotify ingest helpers — listen_actions upsert + thread triple emission.

Lifted from the legacy ``phdb.adapters.spotify`` + ``phdb.adapters.base``
helpers so the plugin doesn't need to inherit the deprecated ``Adapter``
base class. Mirrors the apple_health ``ingest.py`` shape.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from phdb.records import MediaPlay
from phdb.triples import resolve_node

_MAX_BODY_LEN = 2000


def register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "spotify",
    file_kind: str = "json",
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


def build_body_text(rec: MediaPlay) -> str:
    """Render a MediaPlay into a short body_text suitable for FTS / display."""
    body_parts = [rec.title]
    if rec.artist and rec.media_type == "music":
        body_parts = [rec.title.split(" — ")[0]]
        if rec.artist:
            body_parts.append(f"by {rec.artist}")
        if rec.album:
            body_parts.append(f"({rec.album})")
    elif rec.media_type == "podcast":
        body_parts = [f"Podcast: {rec.title}"]
    elif rec.media_type == "audiobook":
        body_parts = [f"Audiobook: {rec.title}"]

    body_text = " ".join(body_parts)
    if len(body_text) > _MAX_BODY_LEN:
        body_text = body_text[:_MAX_BODY_LEN]
    return body_text


def upsert_listen_action(
    conn: sqlite3.Connection,
    source_file_id: int,
    rec: MediaPlay,
) -> int | None:
    """Insert one MediaPlay row into listen_actions. Returns row id or None on dedup."""
    body_text = build_body_text(rec)
    body_text_hash = hashlib.sha256(body_text.encode()).hexdigest()
    artist_name = rec.artist or rec.title
    listen_key = f"spotify:{rec.provenance.raw_hash}"

    cur = conn.execute(
        """INSERT OR IGNORE INTO listen_actions (
            schema_type, listen_key, subject, artist_name,
            source_device, direction, date_listened,
            body_text, body_text_source, body_text_hash,
            is_bulk, bulk_signal, raw_hash, source_file_id
        ) VALUES (
            'ListenAction', ?, ?, ?, 'spotify:self', 'self', ?,
            ?, 'spotify-json', ?, 1, 'spotify-listen-event', ?, ?
        )""",
        (
            listen_key, rec.title, artist_name, rec.date_played,
            body_text, body_text_hash, rec.provenance.raw_hash, source_file_id,
        ),
    )
    if cur.rowcount == 0:
        return None
    return cur.lastrowid


def emit_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    source_table: str,
    message_id: int,
    thread_key: str,
) -> tuple[int, bool]:
    """Emit inThread triple from message record-node to thread-node.

    Returns (thread_node_id, created) — ``created`` is True if the thread
    node didn't exist before this call.
    """
    in_thread_row = conn.execute(
        "SELECT id FROM predicates WHERE name = 'inThread'"
    ).fetchone()
    if in_thread_row:
        in_thread_id = in_thread_row[0]
    else:
        from phdb.triples import get_predicate
        pred = get_predicate(conn, "inThread")
        assert pred is not None
        in_thread_id = pred["id"]

    record_label = f"{source_table}:{message_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table=source_table, source_id=message_id,
    )

    thread_label = f"{source_kind}:{thread_key}"
    existing = conn.execute(
        "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
        (thread_label.lower(),),
    ).fetchone()
    if existing:
        thread_node_id = existing[0]
        created = False
    else:
        thread_node_id = resolve_node(conn, thread_label, "thread")
        created = True

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'adapter', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
    return thread_node_id, created
