"""Tests for migration 0006_ai_sessions."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    return db_path


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _indexes(conn, table: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
    ).fetchall()
    return {r[0] for r in rows}


# ── Column presence ─────────────────────────────────────────────────────────

def test_messages_has_kind_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "kind" in _columns(conn, "messages")


def test_messages_has_role_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "role" in _columns(conn, "messages")


def test_messages_has_parent_uuid_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "parent_uuid" in _columns(conn, "messages")


def test_messages_has_tool_name_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "tool_name" in _columns(conn, "messages")


def test_messages_has_tool_use_id_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "tool_use_id" in _columns(conn, "messages")


def test_messages_has_model_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "model" in _columns(conn, "messages")


def test_messages_has_payload_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "payload" in _columns(conn, "messages")


def test_threads_has_metadata_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "metadata" in _columns(conn, "threads")


def test_threads_has_cwd_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "cwd" in _columns(conn, "threads")


# ── Index presence ───────────────────────────────────────────────────────────

def test_index_messages_kind(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "idx_messages_kind" in _indexes(conn, "messages")


def test_index_messages_kind_date(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "idx_messages_kind_date" in _indexes(conn, "messages")


def test_index_messages_parent_uuid(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "idx_messages_parent_uuid" in _indexes(conn, "messages")


def test_index_messages_tool_use_id(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "idx_messages_tool_use_id" in _indexes(conn, "messages")


def test_index_threads_cwd(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert "idx_threads_cwd" in _indexes(conn, "threads")


# ── Existing columns preserved ───────────────────────────────────────────────

def test_existing_messages_columns_preserved(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        cols = _columns(conn, "messages")
    for col in ("id", "schema_type", "body_text", "date_sent", "sender_address",
                "is_bulk", "raw_hash", "source_file_id"):
        assert col in cols, f"Pre-existing column {col!r} missing after migration"


def test_existing_threads_columns_preserved(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        cols = _columns(conn, "threads")
    for col in ("id", "schema_type", "source_kind", "thread_key",
                "message_count", "date_first", "date_last"):
        assert col in cols, f"Pre-existing column {col!r} missing after migration"


# ── Null defaults on existing rows ───────────────────────────────────────────

def test_new_message_columns_default_null(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO source_files (source_path, file_kind, source_kind) "
            "VALUES ('fake.jsonl', 'jsonl', 'claude-code')"
        )
        src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO messages (schema_type, body_text, source_file_id, raw_hash) "
            "VALUES ('Conversation', 'hello', ?, 'abc123')",
            (src_id,),
        )
        row = conn.execute(
            "SELECT kind, role, parent_uuid, tool_name, tool_use_id, model, payload "
            "FROM messages WHERE raw_hash='abc123'"
        ).fetchone()
    assert all(v is None for v in row), f"Expected all NULL, got {row}"


def test_new_thread_columns_default_null(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO threads (schema_type, source_kind, thread_key, message_count) "
            "VALUES ('Conversation', 'claude-code', 'test-thread-001', 0)"
        )
        row = conn.execute(
            "SELECT metadata, cwd FROM threads WHERE thread_key='test-thread-001'"
        ).fetchone()
    assert row[0] is None and row[1] is None


# ── Round-trip write of AI session data ──────────────────────────────────────

def test_write_and_read_ai_session_message(migrated_db: Path) -> None:
    import json

    payload = {"uuid": "abc-123", "type": "message", "role": "user",
               "content": [{"type": "text", "text": "hello"}]}

    with connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO source_files (source_path, file_kind, source_kind) "
            "VALUES ('session.jsonl', 'jsonl', 'claude-code')"
        )
        src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO messages "
            "(schema_type, body_text, date_sent, kind, role, parent_uuid, model, payload, "
            " source_file_id, raw_hash, is_bulk) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("Conversation", "hello", "2026-05-08T00:00:00Z",
             "message", "user", None, "claude-sonnet-4-6",
             json.dumps(payload), src_id, "hash-001", 0),
        )
        row = conn.execute(
            "SELECT kind, role, model, payload FROM messages WHERE raw_hash='hash-001'"
        ).fetchone()

    assert row[0] == "message"
    assert row[1] == "user"
    assert row[2] == "claude-sonnet-4-6"
    assert json.loads(row[3])["uuid"] == "abc-123"


def test_write_and_read_tool_chain(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO source_files (source_path, file_kind, source_kind) "
            "VALUES ('session2.jsonl', 'jsonl', 'claude-code')"
        )
        src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.executemany(
            "INSERT INTO messages "
            "(schema_type, body_text, date_sent, kind, role, tool_name, tool_use_id, "
            " source_file_id, raw_hash, is_bulk) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("Conversation", None, "2026-05-08T00:01:00Z",
                 "tool_use", "assistant", "Bash", "tuid-xyz", src_id, "hash-tu", 1),
                ("Conversation", "exit 0", "2026-05-08T00:01:01Z",
                 "tool_result", None, "Bash", "tuid-xyz", src_id, "hash-tr", 1),
            ],
        )
        use_row = conn.execute(
            "SELECT kind, tool_name, tool_use_id FROM messages WHERE raw_hash='hash-tu'"
        ).fetchone()
        result_row = conn.execute(
            "SELECT kind, tool_name, tool_use_id FROM messages WHERE raw_hash='hash-tr'"
        ).fetchone()

    assert use_row[0] == "tool_use" and use_row[1] == "Bash" and use_row[2] == "tuid-xyz"
    assert result_row[0] == "tool_result" and result_row[2] == "tuid-xyz"


def test_write_and_read_thread_metadata(migrated_db: Path) -> None:
    import json

    meta = {"gitBranch": "main", "cwd": "/Users/rob/Obsidian", "version": "1.2.3"}

    with connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO threads "
            "(schema_type, source_kind, thread_key, message_count, metadata, cwd) "
            "VALUES (?,?,?,?,?,?)",
            ("Conversation", "claude-code", "session-uuid-001", 0,
             json.dumps(meta), "/Users/rob/Obsidian"),
        )
        row = conn.execute(
            "SELECT metadata, cwd FROM threads WHERE thread_key='session-uuid-001'"
        ).fetchone()

    assert json.loads(row[0])["gitBranch"] == "main"
    assert row[1] == "/Users/rob/Obsidian"


# ── migration_id recorded ────────────────────────────────────────────────────

def test_migration_id_recorded(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        row = conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id='0006_ai_sessions'"
        ).fetchone()
    assert row is not None


# ── EXPLAIN QUERY PLAN uses indexes ─────────────────────────────────────────

def test_explain_kind_index_used(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM messages WHERE kind='message'"
        ).fetchall()
    plan_text = " ".join(" ".join(str(c) for c in tuple(r)) for r in plan).lower()
    assert "idx_messages_kind" in plan_text


def test_explain_cwd_index_used(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO threads (schema_type, source_kind, thread_key, message_count, cwd) "
            "VALUES ('Conversation', 'claude-code', 'cwd-test-thread', 0, '/some/path')"
        )
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM threads WHERE cwd='/some/path'"
        ).fetchall()
    plan_text = " ".join(" ".join(str(c) for c in tuple(r)) for r in plan).lower()
    assert "idx_threads_cwd" in plan_text
