"""Tests for the declared sidecar-table API (Phase 8)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from phdb.adapters.base import (
    Adapter,
    AdapterRow,
    DedupStrategy,
    SidecarColumn,
    SidecarTableDef,
)
from phdb.settings import Settings

TAGS_TABLE = SidecarTableDef(
    table_name="test_tags",
    columns=(
        SidecarColumn("tag", "TEXT", nullable=False),
        SidecarColumn("confidence", "REAL"),
    ),
    parent_fk_column="message_id",
    parent_table="messages",
)

ANNOTATIONS_TABLE = SidecarTableDef(
    table_name="test_annotations",
    columns=(
        SidecarColumn("key", "TEXT", nullable=False),
        SidecarColumn("value", "TEXT"),
        SidecarColumn("score", "INTEGER", default="0"),
    ),
    parent_fk_column="parent_id",
    parent_table="messages",
)


class SidecarTestAdapter(Adapter):
    name = "sidecar_test"
    source_kind = "test-sidecar"
    file_kind = "json"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    sidecar_tables = [TAGS_TABLE, ANNOTATIONS_TABLE]

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield AdapterRow(
            subject="msg with sidecars",
            body_text="test message body",
            date_sent="2024-01-01T00:00:00Z",
            sidecar_rows={
                "test_tags": [
                    {"tag": "health", "confidence": 0.95},
                    {"tag": "exercise", "confidence": 0.80},
                ],
                "test_annotations": [
                    {"key": "category", "value": "fitness", "score": 5},
                ],
            },
        )
        yield AdapterRow(
            subject="msg without sidecars",
            body_text="plain message",
            date_sent="2024-01-02T00:00:00Z",
        )


class NoSidecarAdapter(Adapter):
    name = "no_sidecar_test"
    source_kind = "test-nosidecar"
    file_kind = "json"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield AdapterRow(
            subject="simple",
            body_text="plain",
            date_sent="2024-01-01T00:00:00Z",
        )


@pytest.fixture()
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("""
        CREATE TABLE source_files (
            id INTEGER PRIMARY KEY,
            source_path TEXT UNIQUE NOT NULL,
            source_org TEXT,
            file_kind TEXT,
            source_kind TEXT,
            session_uuid TEXT,
            ingested_at TEXT,
            message_count INTEGER DEFAULT 0,
            UNIQUE(source_kind, session_uuid)
        )
    """)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            schema_type TEXT, rfc822_message_id TEXT UNIQUE,
            in_reply_to TEXT, references_chain TEXT,
            gmail_thread_id TEXT, gmail_labels TEXT,
            subject TEXT, sender_address TEXT, sender_name TEXT, sender_domain TEXT,
            direction TEXT, date_sent TEXT, date_received TEXT,
            body_text TEXT, body_html TEXT, body_text_source TEXT,
            is_multipart INTEGER DEFAULT 0, has_attachments INTEGER DEFAULT 0,
            attachment_count INTEGER DEFAULT 0,
            is_bulk INTEGER DEFAULT 0, bulk_signal TEXT,
            source_file_id INTEGER, source_byte_offset INTEGER, source_byte_length INTEGER,
            raw_hash TEXT, body_text_hash TEXT,
            kind TEXT, role TEXT, parent_uuid TEXT, tool_name TEXT,
            tool_use_id TEXT, model TEXT, payload TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE recipients (
            id INTEGER PRIMARY KEY,
            message_id INTEGER, address TEXT, name TEXT, rtype TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE attachments (
            id INTEGER PRIMARY KEY,
            schema_type TEXT, message_id INTEGER, filename TEXT,
            content_type TEXT, content_disposition TEXT,
            size_bytes INTEGER, on_disk_path TEXT, content_hash TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE threads (
            id INTEGER PRIMARY KEY,
            schema_type TEXT, source_kind TEXT, thread_key TEXT UNIQUE,
            message_count INTEGER DEFAULT 0,
            participants TEXT, metadata TEXT, cwd TEXT,
            date_first TEXT, date_last TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE message_threads (
            message_id INTEGER, thread_id INTEGER,
            UNIQUE(message_id, thread_id)
        )
    """)
    yield conn
    conn.close()


@pytest.fixture()
def settings() -> Settings:
    return Settings(db_path=Path(":memory:"))


class TestSidecarTableDef:
    def test_create_table_sql(self) -> None:
        sql = TAGS_TABLE.create_table_sql()
        assert "CREATE TABLE IF NOT EXISTS test_tags" in sql
        assert "message_id INTEGER NOT NULL REFERENCES messages(id)" in sql
        assert "tag TEXT NOT NULL" in sql
        assert "confidence REAL" in sql

    def test_create_table_sql_with_default(self) -> None:
        sql = ANNOTATIONS_TABLE.create_table_sql()
        assert "score INTEGER DEFAULT 0" in sql

    def test_insert_sql(self) -> None:
        sql = TAGS_TABLE.insert_sql()
        assert sql == "INSERT INTO test_tags (message_id, tag, confidence) VALUES (?, ?, ?)"

    def test_insert_sql_custom_fk(self) -> None:
        sql = ANNOTATIONS_TABLE.insert_sql()
        assert "parent_id" in sql
        assert sql.count("?") == 4  # parent_id + 3 columns


class TestSidecarIntegration:
    def test_sidecar_tables_created(self, db: sqlite3.Connection, settings: Settings) -> None:
        adapter = SidecarTestAdapter()
        source = Path("test.json")
        source.write_text("[]", encoding="utf-8")
        try:
            adapter.run(source, db, settings)
        finally:
            source.unlink(missing_ok=True)

        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "test_tags" in tables
        assert "test_annotations" in tables

    def test_sidecar_rows_inserted(self, db: sqlite3.Connection, settings: Settings) -> None:
        adapter = SidecarTestAdapter()
        source = Path("test.json")
        source.write_text("[]", encoding="utf-8")
        try:
            report = adapter.run(source, db, settings)
        finally:
            source.unlink(missing_ok=True)

        assert report.rows_inserted == 2

        msg_id = db.execute(
            "SELECT id FROM messages WHERE subject='msg with sidecars'"
        ).fetchone()[0]

        tags = db.execute(
            "SELECT message_id, tag, confidence FROM test_tags ORDER BY tag"
        ).fetchall()
        assert len(tags) == 2
        assert tags[0] == (msg_id, "exercise", 0.80)
        assert tags[1] == (msg_id, "health", 0.95)

        annots = db.execute(
            "SELECT parent_id, key, value, score FROM test_annotations"
        ).fetchall()
        assert len(annots) == 1
        assert annots[0] == (msg_id, "category", "fitness", 5)

    def test_no_sidecar_rows_for_plain_message(
        self, db: sqlite3.Connection, settings: Settings
    ) -> None:
        adapter = SidecarTestAdapter()
        source = Path("test.json")
        source.write_text("[]", encoding="utf-8")
        try:
            adapter.run(source, db, settings)
        finally:
            source.unlink(missing_ok=True)

        msg_id = db.execute(
            "SELECT id FROM messages WHERE subject='msg without sidecars'"
        ).fetchone()[0]

        tags = db.execute(
            "SELECT * FROM test_tags WHERE message_id=?", (msg_id,)
        ).fetchall()
        assert tags == []

    def test_adapter_without_sidecar_tables(
        self, db: sqlite3.Connection, settings: Settings
    ) -> None:
        adapter = NoSidecarAdapter()
        source = Path("test.json")
        source.write_text("[]", encoding="utf-8")
        try:
            report = adapter.run(source, db, settings)
        finally:
            source.unlink(missing_ok=True)

        assert report.rows_inserted == 1
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "test_tags" not in tables

    def test_idempotent_table_creation(
        self, db: sqlite3.Connection, settings: Settings
    ) -> None:
        adapter = SidecarTestAdapter()
        adapter.ensure_sidecar_tables(db)
        adapter._sidecar_tables_ensured = False
        adapter.ensure_sidecar_tables(db)  # should not error

    def test_undeclared_sidecar_table_warns(
        self, db: sqlite3.Connection, settings: Settings
    ) -> None:
        adapter = NoSidecarAdapter()
        row = AdapterRow(
            subject="test",
            body_text="test",
            sidecar_rows={"nonexistent_table": [{"col": "val"}]},
        )
        adapter.insert_sidecar_rows(db, 1, row)  # should warn, not crash


class TestSidecarTableDefEdgeCases:
    def test_all_nullable_columns(self) -> None:
        tdef = SidecarTableDef(
            table_name="all_nullable",
            columns=(
                SidecarColumn("a", "TEXT"),
                SidecarColumn("b", "INTEGER"),
            ),
        )
        sql = tdef.create_table_sql()
        assert "a TEXT," in sql
        assert "b INTEGER" in sql
        # User columns should NOT have NOT NULL; FK column does
        assert "a TEXT NOT NULL" not in sql
        assert "b INTEGER NOT NULL" not in sql

    def test_single_column(self) -> None:
        tdef = SidecarTableDef(
            table_name="single_col",
            columns=(SidecarColumn("val", "TEXT", nullable=False),),
        )
        sql = tdef.create_table_sql()
        assert "val TEXT NOT NULL" in sql
        assert sql.strip().endswith(")")
