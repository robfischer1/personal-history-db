"""Tests for the database connection factory."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from phdb.db import connect, ensure_vec_table


def test_connect_creates_db(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    assert not db_path.exists()
    with connect(db_path, create=True) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    assert db_path.exists()


def test_connect_sets_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


def test_connect_sets_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 30000


def test_connect_sets_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


def test_connect_load_vec(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True, load_vec=True) as conn:
        ensure_vec_table(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "doc_vectors" in tables


def test_connect_rejects_missing_db(tmp_path: Path) -> None:
    db_path = tmp_path / "nonexistent.db"
    with pytest.raises(sqlite3.OperationalError), connect(db_path):
        pass


def test_connect_readonly(migrated_db: Path) -> None:
    with connect(migrated_db, readonly=True) as conn:
        count = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
        assert count >= 1
