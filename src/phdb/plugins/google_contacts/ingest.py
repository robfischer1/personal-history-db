"""Google Contacts ingestion logic."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from phdb.formats.person_upserts import emit_person_thread_triple, upsert_person
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.records import Contact

log = get_logger("phdb.plugins.google_contacts.ingest")


def ingest_record(
    conn: sqlite3.Connection,
    record: Contact,
    group: str,
    source_file_id: int,
    *,
    source_kind: str = "google-contacts",
) -> int | None:
    """Ingest a single Contact record as a Person row."""
    # 1. Upsert the main person row
    person_row_id = upsert_person(conn, source_file_id, record, source_kind=source_kind)
    if person_row_id is None:
        return None

    # 2. Emit thread triple
    emit_person_thread_triple(conn, source_kind, person_row_id, group)

    return person_row_id
