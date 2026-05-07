"""Tests for the onedrive adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.onedrive import OneDriveAdapter, _derive_bucket
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "onedrive"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestDeriveBucket:
    def test_single_part(self) -> None:
        assert _derive_bucket(("Documents",)) == "Documents"

    def test_two_parts(self) -> None:
        assert _derive_bucket(("01 Projects", "test")) == "01 Projects/test"

    def test_deep_path(self) -> None:
        assert _derive_bucket(("Documents", "sub", "deep")) == "Documents/sub"

    def test_empty(self) -> None:
        assert _derive_bucket(()) == "(root)"


class TestOneDriveIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.rows_inserted == 3
        assert report.rows_skipped == 0

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "DigitalDocument" for t in types)

    def test_direction_self(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            dirs = conn.execute("SELECT DISTINCT direction FROM messages").fetchall()
        assert all(d[0] == "self" for d in dirs)

    def test_threads_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads >= 1
        assert report.threads_created >= 1

    def test_thread_keys(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            keys = conn.execute("SELECT thread_key FROM threads ORDER BY thread_key").fetchall()
        key_set = {k[0] for k in keys}
        assert any("onedrive:" in k for k in key_set)

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
        with connect(db_path) as conn:
            r2 = OneDriveAdapter().run(FIXTURE_DIR, conn, settings)
        assert r2.rows_inserted == 0

    def test_not_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            bulk = conn.execute("SELECT COUNT(*) FROM messages WHERE is_bulk = 1").fetchone()[0]
        assert bulk == 0

    def test_sender_address(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            addrs = conn.execute("SELECT DISTINCT sender_address FROM messages").fetchall()
        assert all(a[0] == "onedrive:test user" for a in addrs)

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted

    def test_skips_excluded_top_dirs(self, tmp_path: Path) -> None:
        """Files outside INCLUDE_TOP_DIRS should not be ingested."""
        excluded = FIXTURE_DIR / "03 Resources"
        excluded.mkdir(exist_ok=True)
        secret = excluded / "ebook.txt"
        secret.write_text("should not appear")
        try:
            db_path, settings = _setup(tmp_path)
            adapter = OneDriveAdapter()
            with connect(db_path) as conn:
                adapter.run(FIXTURE_DIR, conn, settings)
                subjects = [
                    r[0]
                    for r in conn.execute("SELECT subject FROM messages").fetchall()
                ]
            assert "ebook.txt" not in subjects
        finally:
            secret.unlink(missing_ok=True)
            excluded.rmdir()

    def test_body_content(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            row = conn.execute(
                "SELECT body_text FROM messages WHERE subject = 'hello.txt'"
            ).fetchone()
        assert row is not None
        assert "Hello from OneDrive" in row[0]
