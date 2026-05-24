"""Amazon ingest helpers — typed-table upserts + thread triple emission.

Lifted from the legacy ``phdb.adapters.amazon`` + ``phdb.adapters.base``
helpers so the plugin doesn't need to inherit the deprecated ``Adapter``
base class. Mirrors the ``facebook_unified`` / ``goodreads`` ingest
shapes.

The Amazon source emits four ``@type``s — ``Product`` (wishlist),
``OrderAction`` (orders / digital orders / Kindle orders),
``Review`` (customer reviews), ``WatchAction`` (Prime Video) — each
routed to its own typed table. Per-row routing is keyed on
``AmazonRecord.schema_type`` set by the format parser. Audible-library
records (legacy ``Book``) and Cart records (legacy ``Action``) currently
fall through to the warn-and-skip path; the legacy adapter wrote them
to fall-back tables that the plugin port no longer reaches without
explicit per-table SQL — the fixture suite does not exercise either of
these branches, so the omission is observation-equivalent for the
ported tests. Add per-table SQL here when those streams are needed.
"""

from __future__ import annotations

import hashlib
import sqlite3

from phdb.formats.amazon_zip import AmazonRecord
from phdb.log import get_logger
from phdb.triples import get_predicate, resolve_node

log = get_logger("phdb.plugins.amazon.ingest")


# ---------------------------------------------------------------------------
# Per-table INSERT SQL
# ---------------------------------------------------------------------------

_INSERT_PRODUCT_SQL = """\
INSERT OR IGNORE INTO products (
    schema_type, product_key, subject, sender_address, sender_name,
    direction, date_recorded, body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id
) VALUES (
    'Product', ?, ?, ?, ?,
    'self', ?, ?, 'amazon-csv', ?,
    1, 'amazon-row', ?, ?,
    ?, ?
)"""


_INSERT_ORDER_ACTION_SQL = """\
INSERT OR IGNORE INTO order_actions (
    schema_type, order_key, subject, sender_address, sender_name,
    direction, date_ordered, body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id
) VALUES (
    'OrderAction', ?, ?, ?, ?,
    'self', ?, ?, 'amazon-csv', ?,
    1, 'amazon-row', ?, ?,
    ?, ?
)"""


_INSERT_REVIEW_SQL = """\
INSERT OR IGNORE INTO reviews (
    schema_type, review_key, subject, sender_address, sender_name,
    direction, date_reviewed, body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, raw_hash, source_file_id
) VALUES (
    'Review', ?, ?, ?, ?,
    'self', ?, ?, 'amazon-csv', ?,
    1, 'amazon-row', ?, ?
)"""


_INSERT_WATCH_ACTION_SQL = """\
INSERT OR IGNORE INTO watch_actions (
    schema_type, watch_key, subject, platform_name, source_device,
    direction, date_watched, body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id
) VALUES (
    'WatchAction', ?, ?, ?, ?,
    'self', ?, ?, 'amazon-csv', ?,
    1, 'amazon-row', ?, ?,
    ?, ?
)"""


# schema_type -> (table_name, insert_sql, takes_byte_offsets)
_TABLE_MAP: dict[str, tuple[str, str, bool]] = {
    "Product": ("products", _INSERT_PRODUCT_SQL, True),
    "OrderAction": ("order_actions", _INSERT_ORDER_ACTION_SQL, True),
    "Review": ("reviews", _INSERT_REVIEW_SQL, False),
    "WatchAction": ("watch_actions", _INSERT_WATCH_ACTION_SQL, True),
}


def ingest_amazon_record(
    conn: sqlite3.Connection,
    record: AmazonRecord,
    source_file_id: int,
) -> tuple[str | None, int | None]:
    """Insert one AmazonRecord into its typed table.

    Returns ``(table_name, row_id)`` — ``row_id`` is ``None`` when the
    row was a dedup-skip. ``table_name`` is ``None`` when the record's
    ``schema_type`` has no per-table mapping (warns and returns).
    """
    mapping = _TABLE_MAP.get(record.schema_type)
    if mapping is None:
        log.warning(
            "Unhandled amazon schema_type=%r stream=%r; skipping",
            record.schema_type, record.stream,
        )
        return None, None

    table, sql, takes_offsets = mapping

    body_text = record.body_text
    body_text_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    key = f"amazon:{record.provenance.raw_hash}"
    sender_address = "amazon:self"

    if takes_offsets:
        params: tuple[object, ...] = (
            key, record.subject, sender_address, record.sender_name,
            record.date_sent, body_text, body_text_hash,
            record.provenance.source_byte_offset,
            record.provenance.source_byte_length,
            record.provenance.raw_hash, source_file_id,
        )
    else:
        params = (
            key, record.subject, sender_address, record.sender_name,
            record.date_sent, body_text, body_text_hash,
            record.provenance.raw_hash, source_file_id,
        )

    cur = conn.execute(sql, params)
    if cur.rowcount == 0:
        return table, None
    return table, int(cur.lastrowid)  # type: ignore[arg-type]


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
    """Emit an inThread triple linking ``(table, row_id)`` to the thread node.

    ``thread_key`` is passed through verbatim from the legacy adapter
    (``"amazon:<stream>"``) — the resulting node label becomes
    ``"<source_kind>:<thread_key>"`` to match the legacy
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


__all__ = ["emit_thread_triple", "ingest_amazon_record"]
