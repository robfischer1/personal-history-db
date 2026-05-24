"""Tests for the session_buddy plugin (migration 0034).

Session Buddy exports (nxs.json.v2) capture browser cognitive-state
snapshots.  A tab appearing in N snapshots = N dwell-time intervals.
Snapshots are NOT deduped; source_id is the per-session dedup key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.session_buddy import SessionBuddyPlugin
from phdb.settings import IdentitySettings, Settings

FIXTURE = Path(__file__).parent / "fixtures" / "session_buddy" / "session-buddy-fixture.json"

# The fixture has:
#   3 history entries (2 snapshot-scheduled, 1 browser-closed)
#   2 collections
# Total sessions = 5
# Tabs:
#   snapshot 1714339790627: 2 tabs (1 window)
#   browser-closed 1714332672938: 3 tabs (2 windows)
#   snapshot 1714300000000: 1 tab (1 window)
#   collection coll_research_abc123: 2 tabs (1 folder)
#   collection coll_reading_xyz789: 1 tab (1 folder)
# Total tabs = 2 + 3 + 1 + 2 + 1 = 9


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _new_plugin() -> SessionBuddyPlugin:
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = (
        Path("src/phdb/plugins/session_buddy/plugin.toml").resolve()
    )
    manifest = load_manifest(manifest_path)
    return SessionBuddyPlugin(manifest)


class TestSessionBuddyIngest:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE, conn, settings)
        assert report.rows_yielded == 5
        assert report.rows_inserted == 5
        assert report.rows_skipped == 0

    def test_session_count(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM browser_sessions").fetchone()[0]
        assert count == 5

    def test_tab_count(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM session_tabs").fetchone()[0]
        assert count == 9

    def test_session_types(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            rows = conn.execute(
                "SELECT session_type, COUNT(*) FROM browser_sessions GROUP BY session_type ORDER BY session_type"
            ).fetchall()
        types = {r[0]: r[1] for r in rows}
        assert types.get("snapshot-scheduled") == 2
        assert types.get("browser-closed") == 1
        assert types.get("collection") == 2

    def test_history_timestamps(self, tmp_path: Path) -> None:
        """History session timestamp is stored as Unix ms from the id field."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            row = conn.execute(
                "SELECT timestamp FROM browser_sessions WHERE source_id = '1714339790627'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1714339790627

    def test_collection_timestamp(self, tmp_path: Path) -> None:
        """Collection timestamp is stored as Unix ms from the created field."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            row = conn.execute(
                "SELECT timestamp FROM browser_sessions WHERE source_id = 'coll_research_abc123'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1714200000000

    def test_window_and_tab_counts_on_session(self, tmp_path: Path) -> None:
        """browser_sessions.window_count and tab_count match the fixture."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            row = conn.execute(
                "SELECT window_count, tab_count FROM browser_sessions WHERE source_id = '1714332672938'"
            ).fetchone()
        assert row is not None
        assert row[0] == 2   # 2 windows in browser-closed snapshot
        assert row[1] == 3   # 3 tabs total

    def test_tab_fk_integrity(self, tmp_path: Path) -> None:
        """Every session_tab has a valid session_id FK."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            orphans = conn.execute(
                """SELECT st.id FROM session_tabs st
                   LEFT JOIN browser_sessions bs ON bs.id = st.session_id
                   WHERE st.session_id IS NULL OR bs.id IS NULL"""
            ).fetchall()
        assert orphans == []

    def test_active_flag(self, tmp_path: Path) -> None:
        """Tabs with active=true in the fixture are stored with active=1."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            active_rows = conn.execute(
                "SELECT url, active FROM session_tabs WHERE active = 1"
            ).fetchall()
        urls = {r[0] for r in active_rows}
        # browser-closed snapshot has url sqlite.org/json1.html with active=true
        assert "https://sqlite.org/json1.html" in urls
        # collection coll_reading_xyz789 also has sqlite.org/json1.html active=true
        assert len(active_rows) >= 2  # at least 2 active tabs across all sessions

    def test_window_index_and_tab_index(self, tmp_path: Path) -> None:
        """Tab ordering within multi-window snapshot is correct."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            # browser-closed has 2 windows; get tabs with window_index=1
            rows = conn.execute(
                """SELECT window_index, tab_index, url
                   FROM session_tabs
                   WHERE session_id = (
                       SELECT id FROM browser_sessions WHERE source_id = '1714332672938'
                   )
                   ORDER BY window_index, tab_index"""
            ).fetchall()
        assert len(rows) == 3
        assert tuple(rows[0]) == (0, 0, "https://docs.python.org/3/library/dataclasses.html")
        assert tuple(rows[1]) == (1, 0, "https://sqlite.org/json1.html")
        assert tuple(rows[2]) == (1, 1, "https://sqlite.org/docs.html")

    def test_schema_type_columns(self, tmp_path: Path) -> None:
        """schema_type is set correctly on both tables."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            bs_types = conn.execute(
                "SELECT DISTINCT schema_type FROM browser_sessions"
            ).fetchall()
            st_types = conn.execute(
                "SELECT DISTINCT schema_type FROM session_tabs"
            ).fetchall()
        assert {r[0] for r in bs_types} == {"BrowserSession"}
        assert {r[0] for r in st_types} == {"SessionTab"}

    def test_source_file_registered(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE, conn, settings)
            row = conn.execute(
                "SELECT id, source_kind FROM source_files WHERE id = ?",
                (report.source_file_id,),
            ).fetchone()
        assert row is not None
        assert row[1] == "session_buddy"

    def test_source_file_id_propagated_to_sessions(self, tmp_path: Path) -> None:
        """All browser_sessions rows carry the correct source_file_id."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE, conn, settings)
            null_count = conn.execute(
                "SELECT COUNT(*) FROM browser_sessions WHERE source_file_id IS NULL"
            ).fetchone()[0]
            mismatch = conn.execute(
                "SELECT COUNT(*) FROM browser_sessions WHERE source_file_id != ?",
                (report.source_file_id,),
            ).fetchone()[0]
        assert null_count == 0
        assert mismatch == 0

    def test_source_file_id_propagated_to_tabs(self, tmp_path: Path) -> None:
        """All session_tabs rows carry the correct source_file_id."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE, conn, settings)
            null_count = conn.execute(
                "SELECT COUNT(*) FROM session_tabs WHERE source_file_id IS NULL"
            ).fetchone()[0]
        assert null_count == 0


class TestSessionBuddyIdempotency:
    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        """Running the same export twice produces no duplicate sessions."""
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE, conn, settings)
        with connect(db_path) as conn:
            r2 = _new_plugin().run(FIXTURE, conn, settings)
        assert r2.rows_skipped == 5       # all 5 sessions already present
        assert r2.rows_inserted == 0
        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM browser_sessions").fetchone()[0]
        assert count == 5

    def test_tabs_not_duplicated_on_rerun(self, tmp_path: Path) -> None:
        """Tabs are not re-inserted on a duplicate-skipped rerun."""
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE, conn, settings)
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM session_tabs").fetchone()[0]
        assert count == 9


class TestSessionBuddyDiscovery:
    def test_plugin_loads_via_entry_point(self) -> None:
        from phdb.core.plugin import discover_plugins, load_plugin

        descriptors = discover_plugins()
        sb_desc = next((d for d in descriptors if d.name == "session_buddy"), None)
        assert sb_desc is not None, "session_buddy not found in discover_plugins()"
        plugin = load_plugin(sb_desc)
        assert isinstance(plugin, SessionBuddyPlugin)

    def test_discover_file_directly(self, tmp_path: Path) -> None:
        plugin = _new_plugin()
        results = list(plugin.discover(FIXTURE))
        assert len(results) == 1
        assert results[0][0] == FIXTURE
        assert results[0][1] == "session_buddy"

    def test_discover_directory(self, tmp_path: Path) -> None:
        import shutil

        fixture_dir = tmp_path / "exports"
        fixture_dir.mkdir()
        shutil.copy(FIXTURE, fixture_dir / "session-buddy-export-test.json")
        plugin = _new_plugin()
        results = list(plugin.discover(fixture_dir))
        assert len(results) == 1
        assert results[0][1] == "session_buddy"

    def test_discover_skips_non_session_buddy_json(self, tmp_path: Path) -> None:
        """discover() should only match session-buddy-export*.json files."""
        other = tmp_path / "other-export.json"
        other.write_text('{"format": "something-else"}', encoding="utf-8")
        plugin = _new_plugin()
        results = list(plugin.discover(tmp_path))
        assert results == []

    def test_register_cli_is_noop(self) -> None:
        plugin = _new_plugin()
        assert plugin.register_cli(object()) is None

    def test_register_tools_is_noop(self) -> None:
        plugin = _new_plugin()
        assert plugin.register_tools(object()) is None
