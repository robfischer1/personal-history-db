"""Tests for migrations 0007-0009 (typed tables reshape).

0007: documents → chunks rename
0008: CREATE documents typed table
0009: migrate DigitalDocument rows from messages → documents

After migration 0022, the monolithic messages table is dropped. Tests that
previously verified rows were removed from messages now verify the messages
table no longer exists and rows landed in the correct typed tables.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _indexes(conn, table: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
    ).fetchall()
    return {r[0] for r in rows}


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    return db_path


# ── 0007: chunks rename ────────────────────────────────────────────────────


class TestChunksRename:
    def test_chunks_table_exists(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            assert "chunks" in _tables(conn)

    def test_old_documents_chunk_table_gone(self, migrated_db: Path) -> None:
        """documents exists but as the typed table (0008), not the old chunk registry."""
        with connect(migrated_db) as conn:
            cols = _columns(conn, "documents")
            assert "file_path" in cols
            assert "chunk_index" not in cols

    def test_chunks_has_expected_columns(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            cols = _columns(conn, "chunks")
        for c in ("id", "schema_type", "source_table", "source_id", "chunk_index",
                   "content", "title", "embedding_model", "embedded_at"):
            assert c in cols, f"Missing column {c!r} in chunks"

    def test_chunks_indexes(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            idxs = _indexes(conn, "chunks")
        assert "idx_chunks_source" in idxs
        assert "idx_chunks_schema_type" in idxs
        assert "idx_chunks_embedded_at" in idxs
        assert "idx_chunks_src_chunk" in idxs

    def test_old_indexes_gone(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            all_idx = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()}
        for old in ("idx_documents_source", "idx_documents_schema_type",
                     "idx_documents_embedded_at", "idx_documents_src_chunk"):
            assert old not in all_idx, f"Old index {old!r} still exists"

    def test_fts_points_to_chunks(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='doc_fts'"
            ).fetchone()
        assert sql is not None
        assert "chunks" in sql[0].lower()

    def test_triggers_renamed(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            triggers = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()}
        assert "chunks_ai" in triggers
        assert "chunks_ad" in triggers
        assert "chunks_au" in triggers
        for old in ("documents_ai", "documents_ad", "documents_au"):
            assert old not in triggers

    def test_migration_id_recorded(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE migration_id='0007_chunks_rename'"
            ).fetchone()
        assert row is not None


# ── 0008: documents typed table ────────────────────────────────────────────


class TestDocumentsTypedTable:
    def test_documents_table_exists(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            assert "documents" in _tables(conn)

    def test_documents_columns(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            cols = _columns(conn, "documents")
        for c in ("id", "schema_type", "rfc822_message_id", "subject",
                   "file_path", "file_size", "mtime", "ctime",
                   "body_text", "body_text_source", "body_text_hash",
                   "raw_hash", "is_bulk", "source_file_id", "bucket", "created_at"):
            assert c in cols, f"Missing column {c!r} in documents"

    def test_no_message_columns(self, migrated_db: Path) -> None:
        """documents should not have message-specific columns."""
        with connect(migrated_db) as conn:
            cols = _columns(conn, "documents")
        for c in ("sender_address", "sender_name", "direction",
                   "date_received", "gmail_thread_id"):
            assert c not in cols, f"Unexpected column {c!r} in documents"

    def test_documents_indexes(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            idxs = _indexes(conn, "documents")
        assert "idx_documents_dedup" in idxs
        assert "idx_documents_path" in idxs
        assert "idx_documents_bucket" in idxs

    def test_insert_and_read(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            conn.execute(
                "INSERT INTO source_files (source_path, file_kind, source_kind)"
                " VALUES ('test.zip', 'zip', 'google_drive')"
            )
            src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO documents (schema_type, subject, file_path, bucket,"
                " body_text, raw_hash, source_file_id)"
                " VALUES ('DigitalDocument', 'test.txt', 'My Files/test.txt',"
                " 'My Files', 'Hello', 'abc123', ?)",
                (src_id,),
            )
            row = conn.execute(
                "SELECT subject, file_path, bucket FROM documents WHERE raw_hash='abc123'"
            ).fetchone()
        assert row[0] == "test.txt"
        assert row[1] == "My Files/test.txt"
        assert row[2] == "My Files"

    def test_dedup_unique_constraint(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            conn.execute(
                "INSERT INTO source_files (source_path, file_kind, source_kind)"
                " VALUES ('test.zip', 'zip', 'google_drive')"
            )
            src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO documents (schema_type, raw_hash, source_file_id)"
                " VALUES ('DigitalDocument', 'dup', ?)",
                (src_id,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO documents (schema_type, raw_hash, source_file_id)"
                " VALUES ('DigitalDocument', 'dup', ?)",
                (src_id,),
            )
            count = conn.execute("SELECT COUNT(*) FROM documents WHERE raw_hash='dup'").fetchone()[0]
        assert count == 1

    def test_migration_id_recorded(self, migrated_db: Path) -> None:
        with connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE migration_id='0008_documents_typed_table'"
            ).fetchone()
        assert row is not None


# ── 0009: migrate DigitalDocument rows ─────────────────────────────────────


@pytest.fixture
def pre_reshape_db(tmp_path: Path) -> Path:
    """DB with migrations up to 0006 + synthetic DigitalDocument rows in messages."""
    db_path = tmp_path / "reshape_test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        # Apply only up to 0006
        for m in runner.discover():
            if m.number > 6:
                break
            runner.apply_one(m)

        conn.execute(
            "INSERT INTO source_files (id, source_path, source_org, file_kind, source_kind)"
            " VALUES (1, '/test/takeout.zip', 'google_drive', 'zip', 'google_drive')"
        )
        conn.execute(
            "INSERT INTO source_files (id, source_path, source_org, file_kind, source_kind)"
            " VALUES (2, '/test/inbox.mbox', 'gmail', 'mbox', 'gmail')"
        )

        # DigitalDocument rows (should migrate to documents)
        conn.execute(
            "INSERT INTO messages (id, schema_type, rfc822_message_id, subject,"
            " sender_name, direction, date_sent, body_text, raw_hash, source_file_id, is_bulk)"
            " VALUES (1, 'DigitalDocument', 'gd:abc', 'project.txt',"
            " 'My Files', 'self', '2024-01-01', 'Project content', 'hash1', 1, 0)"
        )
        conn.execute(
            "INSERT INTO messages (id, schema_type, rfc822_message_id, subject,"
            " sender_name, direction, date_sent, body_text, raw_hash, source_file_id, is_bulk)"
            " VALUES (2, 'DigitalDocument', 'gd:def', 'notes.md',"
            " 'Notes', 'self', '2024-02-01', 'Notes content', 'hash2', 1, 0)"
        )

        # EmailMessage row (should stay in messages)
        conn.execute(
            "INSERT INTO messages (id, schema_type, rfc822_message_id, subject,"
            " sender_address, direction, date_sent, body_text, raw_hash, source_file_id, is_bulk)"
            " VALUES (3, 'EmailMessage', 'msg-001', 'Hello',"
            " 'alice@example.com', 'inbound', '2024-03-01', 'Email body', 'hash3', 2, 0)"
        )

        # Chunks for the DigitalDocument rows
        conn.execute(
            "INSERT INTO documents (id, schema_type, source_table, source_id, chunk_index,"
            " chunk_strategy, title, content, content_hash)"
            " VALUES (1, 'DigitalDocument', 'messages', 1, 0,"
            " 'message_body_512tok', 'project.txt', 'Project content', 'chash1')"
        )
        conn.execute(
            "INSERT INTO documents (id, schema_type, source_table, source_id, chunk_index,"
            " chunk_strategy, title, content, content_hash)"
            " VALUES (2, 'DigitalDocument', 'messages', 2, 0,"
            " 'message_body_512tok', 'notes.md', 'Notes content', 'chash2')"
        )

        # Chunk for the EmailMessage
        conn.execute(
            "INSERT INTO documents (id, schema_type, source_table, source_id, chunk_index,"
            " chunk_strategy, title, content, content_hash)"
            " VALUES (3, 'EmailMessage', 'messages', 3, 0,"
            " 'message_body_512tok', 'Hello', 'Email body', 'chash3')"
        )

        # Thread for google_drive rows
        conn.execute(
            "INSERT INTO threads (id, schema_type, source_kind, thread_key, message_count)"
            " VALUES (1, 'Conversation', 'google_drive', 'google-drive:My Files', 2)"
        )
        conn.execute("INSERT INTO message_threads (message_id, thread_id) VALUES (1, 1)")
        conn.execute("INSERT INTO message_threads (message_id, thread_id) VALUES (2, 1)")

        conn.commit()
    return db_path


class TestDocumentsMigrate:
    def test_full_migration_moves_rows(self, pre_reshape_db: Path) -> None:
        with connect(pre_reshape_db) as conn:
            runner = MigrationRunner(conn)
            pending = runner.pending()
            assert len(pending) == 23
            runner.apply_pending()

            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            email_count = conn.execute(
                "SELECT COUNT(*) FROM emails WHERE schema_type='EmailMessage'"
            ).fetchone()[0]
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

        assert doc_count == 2
        assert email_count == 1
        assert "messages" not in tables

    def test_chunks_repointed(self, pre_reshape_db: Path) -> None:
        with connect(pre_reshape_db) as conn:
            MigrationRunner(conn).apply_pending()

            doc_chunks = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_table='documents'"
            ).fetchone()[0]
            email_chunks = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_table='emails'"
            ).fetchone()[0]

        assert doc_chunks == 2
        assert email_chunks == 1

    def test_chunk_source_ids_valid(self, pre_reshape_db: Path) -> None:
        with connect(pre_reshape_db) as conn:
            MigrationRunner(conn).apply_pending()

            orphans = conn.execute(
                "SELECT COUNT(*) FROM chunks c"
                " WHERE c.source_table = 'documents'"
                "   AND c.source_id NOT IN (SELECT id FROM documents)"
            ).fetchone()[0]

        assert orphans == 0

    def test_bucket_recovered_from_sender_name(self, pre_reshape_db: Path) -> None:
        with connect(pre_reshape_db) as conn:
            MigrationRunner(conn).apply_pending()

            row = conn.execute(
                "SELECT bucket FROM documents WHERE rfc822_message_id='gd:abc'"
            ).fetchone()

        assert row is not None
        assert row[0] == "My Files"

    def test_threads_table_dropped_by_0022(self, pre_reshape_db: Path) -> None:
        with connect(pre_reshape_db) as conn:
            MigrationRunner(conn).apply_pending()

            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

        assert "threads" not in tables

    def test_email_messages_migrated_to_emails(self, pre_reshape_db: Path) -> None:
        with connect(pre_reshape_db) as conn:
            MigrationRunner(conn).apply_pending()

            row = conn.execute(
                "SELECT subject, sender_address FROM emails WHERE rfc822_message_id='msg-001'"
            ).fetchone()

        assert row[0] == "Hello"
        assert row[1] == "alice@example.com"

    def test_migration_ids_recorded(self, pre_reshape_db: Path) -> None:
        with connect(pre_reshape_db) as conn:
            MigrationRunner(conn).apply_pending()

            ids = {r[0] for r in conn.execute(
                "SELECT migration_id FROM schema_migrations"
            ).fetchall()}

        assert "0007_chunks_rename" in ids
        assert "0008_documents_typed_table" in ids
        assert "0009_documents_migrate" in ids
