"""Tests for the google_drive adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.google_drive import (
    GoogleDriveAdapter,
    derive_bucket,
    extract_csv,
    extract_json,
    extract_txt,
)
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "google_drive" / "takeout.zip"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestExtractors:
    def test_extract_txt(self) -> None:
        assert extract_txt(b"hello world") == "hello world"

    def test_extract_json(self) -> None:
        result = extract_json(b'{"key": "value"}')
        assert "key" in result
        assert "value" in result

    def test_extract_csv(self) -> None:
        result = extract_csv(b"a,b\n1,2\n")
        assert "a" in result
        assert "1" in result

    def test_extract_txt_unicode(self) -> None:
        assert "caf" in extract_txt("café".encode())


class TestDeriveBucket:
    def test_standard_path(self) -> None:
        assert derive_bucket("Takeout/Drive/My Files/test.txt") == "My Files"

    def test_numbered_prefix(self) -> None:
        assert derive_bucket("Takeout/Drive/01 Projects/foo/bar.txt") == "01 Projects/foo"

    def test_root_file(self) -> None:
        assert derive_bucket("Takeout/Drive/file.txt") == "(root)"

    def test_nested_path(self) -> None:
        assert derive_bucket("Takeout/Drive/Projects/Deep/nested.txt") == "Projects"


class TestGoogleDriveIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
        assert report.rows_inserted == 5
        assert report.rows_skipped == 0

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "DigitalDocument" for t in types)

    def test_direction_self(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            dirs = conn.execute("SELECT DISTINCT direction FROM messages").fetchall()
        assert all(d[0] == "self" for d in dirs)

    def test_threads_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads >= 1
        assert report.threads_created >= 1

    def test_thread_keys(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            keys = conn.execute("SELECT thread_key FROM threads ORDER BY thread_key").fetchall()
        key_set = {k[0] for k in keys}
        assert "google-drive:My Files" in key_set

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            r2 = GoogleDriveAdapter().run(FIXTURE_ZIP, conn, settings)
        assert r2.rows_inserted == 0

    def test_not_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            bulk = conn.execute("SELECT COUNT(*) FROM messages WHERE is_bulk = 1").fetchone()[0]
        assert bulk == 0

    def test_skips_binary_and_trash(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            subjects = [
                r[0]
                for r in conn.execute("SELECT subject FROM messages").fetchall()
            ]
        assert "photo.jpg" not in subjects
        assert "deleted.txt" not in subjects
        assert "Copy of template.txt" not in subjects

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted

    def test_body_content(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            row = conn.execute(
                "SELECT body_text FROM messages WHERE subject = 'test.txt'"
            ).fetchone()
        assert row is not None
        assert "Hello world" in row[0]
