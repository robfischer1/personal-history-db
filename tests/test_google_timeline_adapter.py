"""Tests for the google_timeline adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.google_timeline import GoogleTimelineAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_JSON = Path(__file__).parent / "fixtures" / "google_timeline" / "locationhistory.json"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestGoogleTimelineIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelineAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_JSON, conn, settings)
        assert report.rows_inserted == 3

    def test_schema_types(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelineAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
            types = {t[0] for t in conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()}
        assert "Place" in types
        assert "TravelAction" in types
        assert "GeoShape" in types

    def test_geo_traces_written(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelineAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
            traces = conn.execute("SELECT COUNT(*) FROM geo_traces").fetchone()[0]
        assert traces == 3

    def test_single_thread(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelineAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads == 1

    def test_all_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelineAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
            bulk = conn.execute("SELECT DISTINCT is_bulk FROM messages").fetchall()
        assert all(b[0] == 1 for b in bulk)

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelineAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
        with connect(db_path) as conn:
            r2 = GoogleTimelineAdapter().run(FIXTURE_JSON, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded
