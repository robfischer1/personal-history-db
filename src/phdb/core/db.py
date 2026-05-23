"""Database connection factory.

Every connection gets WAL mode, busy_timeout=30s, and the full pragma set
that legacy ingesters applied manually. This replaces the 6-line boilerplate
repeated across 30+ scripts.

Moved here from ``phdb.db`` as part of Phase 1 of the phdb Plugin
Architecture plan; the legacy module now re-exports from this location.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

VECTOR_DIM = 768


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply the standard pragma set to a connection."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")
    conn.execute("PRAGMA cache_size = -65536")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row


def _load_vec_ext(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension."""
    conn.enable_load_extension(True)
    import sqlite_vec  # type: ignore[import-untyped]

    sqlite_vec.load(conn)


@contextmanager
def connect(
    db_path: Path | str,
    *,
    load_vec: bool = False,
    readonly: bool = False,
    create: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Open a connection with standard pragmas.

    Args:
        db_path: Path to the SQLite database file.
        load_vec: Load the sqlite-vec extension for vector operations.
        readonly: Open in read-only mode (e.g. for query-only paths).
        create: Allow creating the database if it doesn't exist.
            Only ``migrate`` should pass True; other paths get a clear
            error instead of silently creating a stub file.
    """
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    elif create:
        conn = sqlite3.connect(str(db_path))
    else:
        conn = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)

    try:
        _apply_pragmas(conn)

        if load_vec:
            _load_vec_ext(conn)

        yield conn
    finally:
        conn.close()


def connect_persistent(
    db_path: Path | str,
    *,
    load_vec: bool = False,
) -> sqlite3.Connection:
    """Open a long-lived connection for server processes.

    Unlike ``connect()``, this is *not* a context manager — the caller
    owns ``close()``.  Sets ``check_same_thread=False`` for use in async
    event loops (MCP server) and enables ``Row`` factory for dict-style
    access.
    """
    conn = sqlite3.connect(
        f"file:{db_path}?mode=rw", uri=True, check_same_thread=False
    )
    _apply_pragmas(conn)

    if load_vec:
        _load_vec_ext(conn)

    return conn


def ensure_vec_table(conn: sqlite3.Connection, dim: int = VECTOR_DIM) -> None:
    """Create the doc_vectors virtual table if it doesn't exist.

    This must run after sqlite-vec is loaded — it can't live in a migration
    file because vec0 DDL requires the extension loaded before parsing.
    """
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS doc_vectors USING vec0(embedding float[{dim}])"
    )
    conn.commit()
