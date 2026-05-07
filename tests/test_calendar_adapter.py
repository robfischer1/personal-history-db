"""Tests for the calendar adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.calendar import CalendarAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ICS = Path(__file__).parent / "fixtures" / "calendar" / "test.ics"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestCalendarIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ICS, conn, settings)
        assert report.rows_inserted == 2
        assert report.rows_skipped == 0

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ICS, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "Event" for t in types)

    def test_event_subjects(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ICS, conn, settings)
            subjects = {r[0] for r in conn.execute("SELECT subject FROM messages").fetchall()}
        assert "Team Meeting" in subjects
        assert "Lunch with Alice" in subjects

    def test_thread_per_calendar(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ICS, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads == 1

    def test_uid_based_dedup(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ICS, conn, settings)
        with connect(db_path) as conn:
            r2 = CalendarAdapter().run(FIXTURE_ICS, conn, settings)
        assert r2.rows_inserted == 0

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ICS, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
