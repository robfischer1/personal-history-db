"""Tests for the google_timeline plugin (Phase 7 brief 030 port).

Phase 7 of the phdb Plugin Architecture plan refactored google_timeline
from a legacy ``phdb.adapters.google_timeline`` module into a self-
contained ``phdb.plugins.google_timeline`` plugin under the new
contract. Per Phase 0 Q14 (no shim), the legacy import path is broken;
all callers use the plugin's ``run()`` method now.

Test file kept under the old name (``test_google_timeline_adapter.py``)
for git-history continuity; the contents target the new plugin.
"""

from __future__ import annotations

from pathlib import Path

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.google_timeline import GoogleTimelinePlugin
from phdb.settings import IdentitySettings, Settings

FIXTURE_JSON = Path(__file__).parent / "fixtures" / "google_timeline" / "locationhistory.json"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
        # Fix geo_traces FK: migration 0003 created it with REFERENCES messages(id),
        # but migration 0022 dropped messages. Recreate with FK to travel_actions.
        conn.execute("DROP TABLE IF EXISTS geo_traces")
        conn.execute("""CREATE TABLE IF NOT EXISTS geo_traces (
            id INTEGER PRIMARY KEY,
            parent_message_id INTEGER REFERENCES travel_actions(id) ON DELETE CASCADE,
            source_kind TEXT NOT NULL,
            point_idx INTEGER NOT NULL,
            ts TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            elevation_m REAL,
            speed_mps REAL,
            course REAL,
            horizontal_accuracy_m REAL,
            vertical_accuracy_m REAL,
            extra_json TEXT
        )""")
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestGoogleTimelineIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelinePlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_JSON, conn, settings)
        assert report.rows_inserted == 3

    def test_schema_types(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelinePlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
            types = set()
            for tbl, expected in [("places", "Place"), ("travel_actions", "TravelAction"), ("geo_shapes", "GeoShape")]:
                rows = conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
                if rows > 0:
                    types.add(expected)
        assert "Place" in types
        assert "TravelAction" in types
        assert "GeoShape" in types

    def test_geo_traces_written(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelinePlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
            traces = conn.execute("SELECT COUNT(*) FROM geo_traces").fetchone()[0]
        assert traces == 3

    def test_single_thread(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelinePlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads == 1

    def test_all_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelinePlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
            bulk = set()
            for tbl in ("places", "travel_actions", "geo_shapes"):
                rows = conn.execute(f"SELECT DISTINCT is_bulk FROM [{tbl}]").fetchall()
                bulk.update(r[0] for r in rows)
        assert bulk == {1}

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleTimelinePlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_JSON, conn, settings)
        with connect(db_path) as conn:
            r2 = GoogleTimelinePlugin().run(FIXTURE_JSON, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded
