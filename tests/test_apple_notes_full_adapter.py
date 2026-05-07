"""Tests for the apple_notes_full adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.apple_notes_full import AppleNotesFullAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DB = Path(__file__).parent / "fixtures" / "apple_notes_full" / "NoteStore.sqlite"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestAppleNotesFullIntegration:
    def test_basic_ingest_inserts(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleNotesFullAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DB, conn, settings)
        assert report.rows_inserted == 2

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleNotesFullAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "DigitalDocument" for t in types)

    def test_full_body_preferred(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleNotesFullAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
            row = conn.execute(
                "SELECT body_text, body_text_source FROM messages WHERE subject = 'Test Note'"
            ).fetchone()
        assert row is not None
        assert "full body text" in row[0].lower()
        assert row[1] == "apple-notes-proto"

    def test_snippet_fallback(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleNotesFullAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
            row = conn.execute(
                "SELECT body_text, body_text_source FROM messages WHERE subject = 'Another Note'"
            ).fetchone()
        assert row is not None
        assert row[0] == "Another snippet"
        assert row[1] == "apple-notes-snippet"

    def test_update_existing_row(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO source_files (source_path, file_hash) VALUES ('fake.db', 'fakehash')"
            )
            conn.execute(
                """INSERT INTO messages (rfc822_message_id, schema_type, body_text,
                    body_text_source, raw_hash, body_text_hash, source_file_id)
                VALUES ('notes:1', 'DigitalDocument', 'Short', 'old', 'h1', 'h2', 1)"""
            )
            conn.commit()
        adapter = AppleNotesFullAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DB, conn, settings)
            row = conn.execute(
                "SELECT body_text FROM messages WHERE rfc822_message_id = 'notes:1'"
            ).fetchone()
        assert len(row[0]) > len("Short")
        assert report.rows_inserted >= 1

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleNotesFullAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
        with connect(db_path) as conn:
            r2 = AppleNotesFullAdapter().run(FIXTURE_DB, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded
