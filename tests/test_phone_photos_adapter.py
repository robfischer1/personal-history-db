"""Tests for the phone photos adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.phone_photos import PhonePhotosAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "phone_photos"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestPhonePhotosIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.rows_inserted == 3
        assert report.rows_skipped == 0

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "Photograph" for t in types)

    def test_direction_self(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            dirs = conn.execute("SELECT DISTINCT direction FROM messages").fetchall()
        assert all(d[0] == "self" for d in dirs)

    def test_attachments_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            att_count = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
        assert att_count == 3

    def test_filename_date_parsing(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            rows = conn.execute(
                "SELECT subject, date_sent FROM messages ORDER BY date_sent"
            ).fetchall()
        dated = [(r[0], r[1]) for r in rows if r[1] is not None]
        assert len(dated) >= 2
        assert any("2011" in d for _, d in dated)
        assert any("2012" in d for _, d in dated)

    def test_thread_keys_by_year(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            threads = conn.execute("SELECT thread_key FROM threads").fetchall()
        keys = {t[0] for t in threads}
        assert any("2011" in k for k in keys)
        assert any("2012" in k for k in keys)

    def test_skips_non_media(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.rows_inserted == 3

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
        with connect(db_path) as conn:
            r2 = PhonePhotosAdapter().run(FIXTURE_DIR, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted

    def test_bucket_label(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter(bucket_label="test-bucket")
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            threads = conn.execute("SELECT thread_key FROM threads").fetchall()
        keys = {t[0] for t in threads}
        assert all("test-bucket" in k for k in keys)

    def test_video_content_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            mp4 = conn.execute(
                "SELECT content_type FROM attachments WHERE filename LIKE '%.mp4'"
            ).fetchone()
        assert mp4 is not None
        assert mp4[0] == "video/mp4"
