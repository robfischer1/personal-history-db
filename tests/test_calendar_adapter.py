"""Tests for the calendar plugin (Phase 7 brief 029 port).

Phase 7 of the phdb Plugin Architecture plan refactored calendar from
the legacy ``phdb.adapters.calendar`` module into a self-contained
``phdb.plugins.calendar`` plugin under the new contract. Per Phase 0
Q14 (no shim), the legacy import path is broken; all callers use the
plugin's ``run()`` method now.

Test file kept under the old name (``test_calendar_adapter.py``) for
git-history continuity; the contents target the new plugin.
"""

from __future__ import annotations

from pathlib import Path

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.calendar import CalendarPlugin
from phdb.settings import IdentitySettings, Settings

FIXTURE_ICS = Path(__file__).parent / "fixtures" / "calendar" / "test.ics"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestCalendarIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarPlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ICS, conn, settings)
        assert report.rows_inserted == 2
        assert report.rows_skipped == 0

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ICS, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM events").fetchall()
        assert all(t[0] == "Event" for t in types)

    def test_event_subjects(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ICS, conn, settings)
            subjects = {r[0] for r in conn.execute("SELECT subject FROM events").fetchall()}
        assert "Team Meeting" in subjects
        assert "Lunch with Alice" in subjects

    def test_thread_per_calendar(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ICS, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads == 1

    def test_uid_based_dedup(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ICS, conn, settings)
        with connect(db_path) as conn:
            r2 = CalendarPlugin().run(FIXTURE_ICS, conn, settings)
        assert r2.rows_inserted == 0

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = CalendarPlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ICS, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted
