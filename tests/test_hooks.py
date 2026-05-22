"""Tests for the pre_insert / post_insert hooks in base.Adapter."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.settings import Settings


class _SidecarAdapter(Adapter):
    """Test adapter that uses post_insert to write a sidecar table."""

    name = "test_sidecar"
    source_kind = "test"
    file_kind = "synthetic"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield AdapterRow(
            schema_type="Message",
            sender_address="alice@test.com",
            date_sent="2026-01-01T00:00:00Z",
            body_text="Hello from test",
            raw_hash="unique_hash_001",
            extra={"sidecar_key": "value_one"},
        )
        yield AdapterRow(
            schema_type="Message",
            sender_address="bob@test.com",
            date_sent="2026-01-02T00:00:00Z",
            body_text="Second message",
            raw_hash="unique_hash_002",
            extra={"sidecar_key": "value_two"},
        )

    def post_insert(
        self, conn: sqlite3.Connection, row: AdapterRow, inserted_id: int
    ) -> None:
        conn.execute(
            "INSERT INTO test_sidecar (message_id, key) VALUES (?, ?)",
            (inserted_id, row.extra.get("sidecar_key")),
        )


class _FilterAdapter(Adapter):
    """Test adapter that uses pre_insert to skip certain rows."""

    name = "test_filter"
    source_kind = "test"
    file_kind = "synthetic"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield AdapterRow(
            schema_type="Message",
            sender_address="keep@test.com",
            date_sent="2026-01-01T00:00:00Z",
            body_text="Keep this",
            raw_hash="keep_hash_001",
            extra={"skip": False},
        )
        yield AdapterRow(
            schema_type="Message",
            sender_address="skip@test.com",
            date_sent="2026-01-02T00:00:00Z",
            body_text="Skip this",
            raw_hash="skip_hash_002",
            extra={"skip": True},
        )
        yield AdapterRow(
            schema_type="Message",
            sender_address="keep2@test.com",
            date_sent="2026-01-03T00:00:00Z",
            body_text="Keep this too",
            raw_hash="keep_hash_003",
            extra={"skip": False},
        )

    def pre_insert(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> AdapterRow | None:
        if row.extra.get("skip"):
            return None
        return row


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE source_files (
            id INTEGER PRIMARY KEY,
            source_path TEXT UNIQUE NOT NULL,
            source_org TEXT,
            file_kind TEXT,
            source_kind TEXT,
            session_uuid TEXT,
            file_size INTEGER,
            file_hash TEXT,
            message_count INTEGER DEFAULT 0,
            ingested_at TEXT NOT NULL,
            notes TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_files_session
            ON source_files(source_kind, session_uuid) WHERE session_uuid IS NOT NULL;

        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY,
            schema_type TEXT DEFAULT 'Message',
            message_key TEXT,
            subject TEXT,
            sender_address TEXT,
            sender_name TEXT,
            sender_domain TEXT,
            direction TEXT DEFAULT 'unknown',
            date_sent TEXT,
            date_received TEXT,
            body_text TEXT,
            body_text_source TEXT,
            body_text_hash TEXT,
            is_multipart INTEGER DEFAULT 0,
            has_attachments INTEGER DEFAULT 0,
            attachment_count INTEGER DEFAULT 0,
            is_bulk INTEGER DEFAULT 0,
            bulk_signal TEXT,
            source_byte_offset INTEGER,
            source_byte_length INTEGER,
            raw_hash TEXT,
            source_file_id INTEGER,
            UNIQUE(source_file_id, raw_hash)
        );

        CREATE TABLE recipients (
            id INTEGER PRIMARY KEY,
            message_id INTEGER NOT NULL,
            address TEXT NOT NULL,
            name TEXT,
            rtype TEXT NOT NULL
        );

        CREATE TABLE attachments (
            id INTEGER PRIMARY KEY,
            schema_type TEXT NOT NULL DEFAULT 'DigitalDocument',
            message_id INTEGER NOT NULL,
            filename TEXT,
            content_type TEXT,
            content_disposition TEXT,
            size_bytes INTEGER,
            on_disk_path TEXT,
            content_hash TEXT
        );

        CREATE TABLE threads (
            id INTEGER PRIMARY KEY,
            schema_type TEXT, source_kind TEXT, thread_key TEXT UNIQUE,
            message_count INTEGER DEFAULT 0,
            participants TEXT, metadata TEXT, cwd TEXT,
            date_first TEXT, date_last TEXT
        );

        CREATE TABLE message_threads (
            message_id INTEGER, thread_id INTEGER,
            UNIQUE(message_id, thread_id)
        );

        CREATE TABLE test_sidecar (
            id INTEGER PRIMARY KEY,
            message_id INTEGER NOT NULL,
            key TEXT
        );
    """)
    return conn


@pytest.fixture
def settings() -> Settings:
    return Settings(db_path=Path(":memory:"))


class TestPostInsertHook:
    def test_sidecar_written(self, db: sqlite3.Connection, settings: Settings) -> None:
        adapter = _SidecarAdapter()
        report = adapter.run(Path("/fake/source.txt"), db, settings)

        assert report.rows_inserted == 2

        sidecars = db.execute("SELECT message_id, key FROM test_sidecar ORDER BY key").fetchall()
        assert len(sidecars) == 2
        assert sidecars[0][1] == "value_one"
        assert sidecars[1][1] == "value_two"

    def test_sidecar_has_correct_parent_id(self, db: sqlite3.Connection, settings: Settings) -> None:
        adapter = _SidecarAdapter()
        adapter.run(Path("/fake/source.txt"), db, settings)

        rows = db.execute(
            "SELECT m.id, s.message_id FROM chat_messages m JOIN test_sidecar s ON m.id = s.message_id"
        ).fetchall()
        assert len(rows) == 2
        for msg_id, sidecar_msg_id in rows:
            assert msg_id == sidecar_msg_id


class TestPreInsertHook:
    def test_rows_filtered(self, db: sqlite3.Connection, settings: Settings) -> None:
        adapter = _FilterAdapter()
        report = adapter.run(Path("/fake/source.txt"), db, settings)

        assert report.rows_yielded == 3
        assert report.rows_inserted == 2
        assert report.rows_skipped == 1

    def test_only_kept_rows_in_db(self, db: sqlite3.Connection, settings: Settings) -> None:
        adapter = _FilterAdapter()
        adapter.run(Path("/fake/source.txt"), db, settings)

        rows = db.execute("SELECT sender_address FROM chat_messages ORDER BY date_sent").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "keep@test.com"
        assert rows[1][0] == "keep2@test.com"
