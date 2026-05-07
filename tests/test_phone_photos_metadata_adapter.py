"""Tests for the phone photos metadata adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.phone_photos_metadata import PhonePhotosMetadataAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_TAR = (
    Path(__file__).parent
    / "fixtures"
    / "phone_photos_metadata"
    / "com.android.providers.media-test.tar.gz"
)


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestPhonePhotosMetadataIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_TAR, conn, settings)
        assert report.rows_inserted == 3
        assert report.rows_skipped == 0

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "Photograph" for t in types)

    def test_direction_self(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            dirs = conn.execute("SELECT DISTINCT direction FROM messages").fetchall()
        assert all(d[0] == "self" for d in dirs)

    def test_body_has_metadata(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            bodies = conn.execute("SELECT body_text FROM messages").fetchall()
        for b in bodies:
            assert "folder=" in b[0]
            assert "path=" in b[0]

    def test_gps_in_body(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            bodies = conn.execute("SELECT body_text FROM messages").fetchall()
        gps_rows = [b[0] for b in bodies if "gps=" in b[0]]
        assert len(gps_rows) == 1
        assert "40.7128" in gps_rows[0]

    def test_datetaken_preferred(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            row = conn.execute(
                "SELECT date_sent FROM messages WHERE subject = 'IMG_20111215.jpg'"
            ).fetchone()
        assert row is not None
        assert "2011-12" in row[0]

    def test_date_added_fallback(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            row = conn.execute(
                "SELECT date_sent FROM messages WHERE subject = 'IMG_20120101.jpg'"
            ).fetchone()
        assert row is not None
        assert "2012-01" in row[0]

    def test_thread_keys(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            threads = conn.execute("SELECT thread_key FROM threads").fetchall()
        keys = {t[0] for t in threads}
        assert any("Camera" in k for k in keys)

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
        with connect(db_path) as conn:
            r2 = PhonePhotosMetadataAdapter().run(FIXTURE_TAR, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhonePhotosMetadataAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_TAR, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
