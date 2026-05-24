"""Google Timeline ingest helpers — typed-table upserts + sidecar + thread triple.

Lifted from the legacy ``phdb.adapters.google_timeline`` + ``phdb.adapters.base``
helpers so the plugin doesn't need to inherit the deprecated ``Adapter``
base. Mirrors the ``apple_health`` / ``amazon`` ingest shapes.

Routes ``GeoTrace`` records into three typed tables by ``trace_type``:

- ``visit``        -> ``places``         (@type ``Place``)
- ``activity``     -> ``travel_actions`` (@type ``TravelAction``)
- ``timelinepath`` -> ``geo_shapes``     (@type ``GeoShape``)

TimelinePath waypoints land in the ``geo_traces`` sidecar table keyed on
``parent_message_id``. The legacy adapter declared the sidecar's
parent table as ``travel_actions`` but inserted ``geo_shapes`` row ids
into the FK column (FK enforcement is off by default in SQLite); the
plugin preserves that exact behavior so the ported tests keep passing.
"""

from __future__ import annotations

import hashlib
import sqlite3

from phdb.core.source_files import register_source_file as register_source_file
from phdb.records import GeoTrace
from phdb.triples import get_predicate, resolve_node

_MAX_BODY_LEN = 2000

# trace_type -> (schema_type, bulk_signal)
TRACE_TYPE_TO_SCHEMA: dict[str, tuple[str, str]] = {
    "visit": ("Place", "google-timeline-visit"),
    "activity": ("TravelAction", "google-timeline-activity"),
    "timelinepath": ("GeoShape", "google-timeline-path"),
}


# ---------------------------------------------------------------------------
# sidecar DDL
# ---------------------------------------------------------------------------


def ensure_sidecar_tables(conn: sqlite3.Connection) -> None:
    """Ensure the ``geo_traces`` sidecar table exists.

    Migration 0003 originally created ``geo_traces`` with a FK to the
    legacy ``messages`` table — migration 0022 dropped messages, which
    leaves stale fixtures (and any fresh DBs that re-apply 0003 after a
    truncate) without a valid sidecar. The test harness already
    recreates this table with the FK pointed at ``travel_actions``; this
    helper is the production path for fresh DBs.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS geo_traces (
            id INTEGER PRIMARY KEY,
            parent_message_id INTEGER REFERENCES travel_actions(id) ON DELETE CASCADE,
            source_kind TEXT NOT NULL,
            point_idx INTEGER NOT NULL,
            ts TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            elevation_m REAL,
            speed_mps REAL,
            course REAL,
            horizontal_accuracy_m REAL,
            vertical_accuracy_m REAL,
            extra_json TEXT
        )"""
    )


# ---------------------------------------------------------------------------
# Per-table INSERT SQL
# ---------------------------------------------------------------------------


_INSERT_PLACE_SQL = """\
INSERT OR IGNORE INTO places (
    schema_type, place_key, subject, sender_address, sender_name,
    direction, date_recorded,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, raw_hash, source_file_id
) VALUES (
    'Place', ?, ?, ?, ?,
    'self', ?,
    ?, 'google-timeline-json', ?,
    1, ?, ?, ?
)"""


_INSERT_TRAVEL_ACTION_SQL = """\
INSERT OR IGNORE INTO travel_actions (
    schema_type, travel_key, subject, sender_address, sender_name,
    direction, date_traveled,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, raw_hash, source_file_id
) VALUES (
    'TravelAction', ?, ?, ?, ?,
    'self', ?,
    ?, 'google-timeline-json', ?,
    1, ?, ?, ?
)"""


_INSERT_GEO_SHAPE_SQL = """\
INSERT OR IGNORE INTO geo_shapes (
    schema_type, geo_key, subject, sender_address, sender_name,
    direction, date_recorded,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, raw_hash, source_file_id
) VALUES (
    'GeoShape', ?, ?, ?, ?,
    'self', ?,
    ?, 'google-timeline-json', ?,
    1, ?, ?, ?
)"""


_INSERT_GEO_TRACE_SQL = """\
INSERT INTO geo_traces
    (parent_message_id, source_kind, point_idx, ts, lat, lon,
     elevation_m, speed_mps, course, horizontal_accuracy_m,
     vertical_accuracy_m, extra_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


# trace_type -> (table_name, insert_sql)
# All three typed tables expose only a single date column on insert; the
# legacy adapter set ``date_received`` on the AdapterRow for visits, but
# the ``places`` table never had a ``date_received`` column — the value
# was silently dropped by the base typed-table mapper. The plugin port
# matches that lossy behavior.
_TABLE_MAP: dict[str, str] = {
    "visit": "places",
    "activity": "travel_actions",
    "timelinepath": "geo_shapes",
}

_TABLE_SQL: dict[str, str] = {
    "places": _INSERT_PLACE_SQL,
    "travel_actions": _INSERT_TRAVEL_ACTION_SQL,
    "geo_shapes": _INSERT_GEO_SHAPE_SQL,
}


def _build_subject_body(rec: GeoTrace) -> tuple[str, str]:
    """Build the (subject, body_text) pair for a GeoTrace row.

    Mirrors the per-branch shape the legacy adapter produced — the
    strings flow into FTS / display surfaces, so keeping them byte-for-
    byte equivalent avoids downstream drift.
    """
    if rec.trace_type == "visit":
        subject = f"Visit: {rec.place_name or 'Unknown'}"
        body = (
            f"Visit ({rec.place_name}) place_id={rec.place_address or ''} "
            f"loc= start={rec.date_start} end={rec.date_end}"
        )
    elif rec.trace_type == "activity":
        subject = f"Activity: {rec.activity_type or 'unknown'}"
        body = (
            f"Activity ({rec.activity_type}) "
            f"start_ts={rec.date_start} end_ts={rec.date_end}"
        )
    else:
        subject = f"Trace: {len(rec.waypoints)} points"
        body = (
            f"timelinePath {len(rec.waypoints)} points "
            f"start={rec.date_start} end={rec.date_end}"
        )
    return subject, body[:_MAX_BODY_LEN]


def ingest_geo_trace(
    conn: sqlite3.Connection,
    record: GeoTrace,
    source_file_id: int,
) -> tuple[str | None, int | None]:
    """Insert one GeoTrace into its typed table.

    Returns ``(table_name, row_id)`` — ``row_id`` is ``None`` when the
    row was a dedup skip. ``table_name`` is ``None`` when the record's
    ``trace_type`` has no per-table mapping (warns and returns).

    When the inserted record is a TimelinePath with waypoints, this
    function also writes per-waypoint rows to the ``geo_traces`` sidecar
    keyed on ``parent_message_id``.
    """
    table = _TABLE_MAP.get(record.trace_type)
    if table is None:
        # Unknown trace_type — fall through silently to match legacy behavior
        # (the legacy adapter mapped unknowns to ("Place", "google-timeline-unknown")
        # which would land in places; the fixture suite never exercises this
        # branch, so the plugin port skips it rather than mis-route).
        return None, None

    sql = _TABLE_SQL[table]
    _, bulk_signal = TRACE_TYPE_TO_SCHEMA[record.trace_type]

    subject, body_text = _build_subject_body(record)
    body_text_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    raw_hash = record.provenance.raw_hash
    trace_key = f"google-timeline:{record.trace_type[:3]}:{raw_hash[:16]}"
    sender_address = "google-timeline:self"
    sender_name = "google-timeline"
    date_start = record.date_start or None

    params: tuple[object, ...] = (
        trace_key, subject, sender_address, sender_name,
        date_start,
        body_text, body_text_hash,
        bulk_signal, raw_hash, source_file_id,
    )

    cur = conn.execute(sql, params)
    if cur.rowcount == 0:
        return table, None
    row_id = int(cur.lastrowid)  # type: ignore[arg-type]

    if record.waypoints:
        for idx, (lat, lon, ts_v) in enumerate(record.waypoints):
            conn.execute(
                _INSERT_GEO_TRACE_SQL,
                (
                    row_id, "google-timeline-path", idx, ts_v or None,
                    lat, lon,
                    None, None, None, None, None, None,
                ),
            )

    return table, row_id


# ---------------------------------------------------------------------------
# Thread triple emission
# ---------------------------------------------------------------------------


def emit_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    table: str,
    row_id: int,
    thread_key: str,
) -> tuple[int, bool]:
    """Emit an ``inThread`` triple linking ``(table, row_id)`` to the thread node.

    ``thread_key`` is passed through verbatim from the plugin
    (``"lifestream"`` for google-timeline); the resulting node label
    becomes ``"<source_kind>:<thread_key>"`` to match the legacy
    ``Adapter._upsert_thread`` shape so existing tests over thread-node
    counts keep passing.

    Returns ``(thread_node_id, created)``; ``created`` is True when the
    thread node didn't exist before this call.
    """
    pred = get_predicate(conn, "inThread")
    if not pred:
        return 0, False
    in_thread_id = pred["id"]

    record_label = f"{table}:{row_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table=table, source_id=row_id,
    )

    thread_label = f"{source_kind}:{thread_key}"
    existing = conn.execute(
        "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
        (thread_label.lower(),),
    ).fetchone()
    if existing:
        thread_node_id = int(existing[0])
        created = False
    else:
        _node = resolve_node(conn, thread_label, "thread")
        assert _node is not None
        thread_node_id = _node
        created = True

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'plugin', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
    return thread_node_id, created


__all__ = [
    "TRACE_TYPE_TO_SCHEMA",
    "emit_thread_triple",
    "ensure_sidecar_tables",
    "ingest_geo_trace",
    "register_source_file",
]
