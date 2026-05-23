"""Person upserts — logic for the persons typed table.

Phase 7: Person remains action-shaped (messages-decomposition).
Identity coalescence in Phase 8 will consume these rows to build
the Person entity graph.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING

from phdb.triples import resolve_node, get_predicate

if TYPE_CHECKING:
    from phdb.records import Contact


def upsert_person(
    conn: sqlite3.Connection,
    source_file_id: int,
    record: Contact,
    *,
    source_kind: str = "google-contacts",
) -> int:
    """Upsert a Person action row into the persons table."""
    body_parts = [record.full_name]
    if record.organization:
        body_parts.append(f"Org: {record.organization}")
    if record.title:
        body_parts.append(f"Title: {record.title}")
    if record.emails:
        body_parts.append(f"Emails: {', '.join(record.emails)}")
    if record.phones:
        body_parts.append(f"Phones: {', '.join(record.phones)}")
    
    body = "\n".join(body_parts)[:5000]
    body_hash = hashlib.sha256(body.encode()).hexdigest()

    primary_addr = (
        record.emails[0] 
        if record.emails 
        else (record.phones[0] if record.phones else record.full_name.lower())
    )

    cur = conn.execute(
        """INSERT INTO persons (
            schema_type, person_key, subject, sender_address, sender_name,
            direction, date_recorded, body_text, body_text_source, body_text_hash,
            is_bulk, bulk_signal, raw_hash, source_file_id
        ) VALUES (
            'Person', ?, ?, ?, ?, 'self', NULL, ?, ?, ?, 1, 'contact-card', ?, ?
        ) ON CONFLICT(source_file_id, raw_hash) DO NOTHING
        RETURNING id""",
        (
            f"{source_kind}:{record.provenance.raw_hash}",
            record.full_name,
            primary_addr,
            record.full_name,
            body,
            f"{source_kind}-vcf",
            body_hash,
            record.provenance.raw_hash,
            source_file_id,
        ),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def emit_person_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    person_row_id: int,
    group: str,
) -> None:
    """Emit an inThread triple linking a Person row to its group thread."""
    pred = get_predicate(conn, "inThread")
    if not pred:
        return
    in_thread_id = pred["id"]

    record_label = f"persons:{person_row_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table="persons", source_id=person_row_id,
    )

    thread_label = f"{source_kind}:{group}"
    thread_node_id = resolve_node(conn, thread_label, "thread")

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'plugin', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
