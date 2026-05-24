"""Shared source-file registration for plugins.

Consolidated from ``_register_source_file`` copies across all plugin.py
files. Callers must pass explicit ``source_kind`` and ``file_kind`` — no
defaults.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str,
    file_kind: str,
    source_org: str | None = None,
    file_size: int | None = None,
) -> int:
    """Insert or refresh a source_files row, returning the row id.

    When *file_size* is provided the row also stores the file size and
    preserves it on conflict-update (used by mbox, google-contacts).
    """
    if file_size is not None:
        cur = conn.execute(
            """INSERT INTO source_files
               (source_path, source_org, file_kind, source_kind, session_uuid,
                file_size, ingested_at)
               VALUES (?, ?, ?, ?, NULL, ?,
                       strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               ON CONFLICT(source_path) DO UPDATE
                 SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                     file_size = excluded.file_size
               RETURNING id""",
            (str(source_path), source_org, file_kind, source_kind, file_size),
        )
    else:
        cur = conn.execute(
            """INSERT INTO source_files
               (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
               VALUES (?, ?, ?, ?, NULL,
                       strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               ON CONFLICT(source_path) DO UPDATE
                 SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               RETURNING id""",
            (str(source_path), source_org, file_kind, source_kind),
        )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])
