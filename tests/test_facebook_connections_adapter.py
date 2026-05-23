"""Tests for the facebook_connections adapter."""

from __future__ import annotations

import json
from pathlib import Path

from phdb.db import connect
from phdb.formats.facebook_connections_html import (
    normalize_name,
)
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.facebook_connections import FacebookConnectionsPlugin
from phdb.plugins.facebook_connections.ingest import make_dedupe_key
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = (
    Path(__file__).parent
    / "fixtures"
    / "facebook_connections"
    / "facebook_connections_test.zip"
)


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _new_plugin() -> FacebookConnectionsPlugin:
    """Build a FacebookConnectionsPlugin with the in-tree manifest."""
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = Path("src/phdb/plugins/facebook_connections/plugin.toml").resolve()
    manifest = load_manifest(manifest_path)
    return FacebookConnectionsPlugin(manifest)


class TestNameNormalization:
    def test_basic(self) -> None:
        assert normalize_name("Alice Johnson") == "alice johnson"

    def test_strips_accents(self) -> None:
        assert normalize_name("Jose Garcia") == "jose garcia"

    def test_strips_punctuation(self) -> None:
        assert normalize_name("O'Brien") == "obrien"

    def test_collapses_whitespace(self) -> None:
        assert normalize_name("  John   Doe  ") == "john doe"

    def test_empty(self) -> None:
        assert normalize_name("") == ""


class TestDedupeKey:
    def test_with_profile_url(self) -> None:
        assert make_dedupe_key("https://facebook.com/alice", "alice") == "url:https://facebook.com/alice"

    def test_without_profile_url(self) -> None:
        assert make_dedupe_key(None, "alice johnson") == "name:alice johnson"


class TestFacebookConnectionsIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        """Count check: 3 friends + 1 removed + 1 sent request = 5 total."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_ZIP, conn, settings)
        assert report.rows_yielded == 5
        assert report.rows_inserted == 5

    def test_connection_statuses(self, tmp_path: Path) -> None:
        """Active, inactive, and pending_outbound statuses all present."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
            statuses = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT connection_status FROM connections"
                ).fetchall()
            }
        assert "active" in statuses
        assert "inactive" in statuses
        assert "pending_outbound" in statuses

    def test_instrument(self, tmp_path: Path) -> None:
        """All rows have instrument='facebook'."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
            instruments = conn.execute(
                "SELECT DISTINCT instrument FROM connections"
            ).fetchall()
        assert len(instruments) == 1
        assert instruments[0][0] == "facebook"

    def test_name_normalization(self, tmp_path: Path) -> None:
        """name_normalized is lowercased and stripped."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
            row = conn.execute(
                "SELECT name_normalized FROM connections WHERE display_name='Alice Johnson'"
            ).fetchone()
        assert row is not None
        assert row[0] == "alice johnson"

    def test_friends_since(self, tmp_path: Path) -> None:
        """Date parsed from HTML for active friends."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
            row = conn.execute(
                "SELECT friends_since FROM connections WHERE display_name='Alice Johnson'"
            ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[0].startswith("2020-01-15")

    def test_dedupe_key(self, tmp_path: Path) -> None:
        """Name-based dedupe key format."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
            row = conn.execute(
                "SELECT dedupe_key FROM connections WHERE display_name='Bob Smith'"
            ).fetchone()
        assert row is not None
        assert row[0] == "name:bob smith"

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        """Second run doesn't duplicate rows."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE_ZIP, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0]
        # Still 5 rows, not 10
        assert count == 5

    def test_source_file_registered(self, tmp_path: Path) -> None:
        """source_files table has an entry for the zip."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_ZIP, conn, settings)
            row = conn.execute(
                "SELECT id, source_kind FROM source_files WHERE id=?",
                (report.source_file_id,),
            ).fetchone()
        assert row is not None
        assert row[1] == "facebook-connections"

    def test_appearance_count_on_rerun(self, tmp_path: Path) -> None:
        """appearance_count incremented on rerun."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE_ZIP, conn, settings)
            counts = conn.execute(
                "SELECT appearance_count FROM connections ORDER BY id"
            ).fetchall()
        assert all(c[0] == 2 for c in counts)

    def test_inactive_reason(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
            row = conn.execute(
                "SELECT inactive_reason FROM connections WHERE display_name='Dave Wilson'"
            ).fetchone()
        assert row is not None
        assert row[0] == "removed_friends_file"

    def test_appearances_json_tracks_history(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE_ZIP, conn, settings)
            row = conn.execute(
                "SELECT appearances_json FROM connections WHERE display_name='Alice Johnson'"
            ).fetchone()
        assert row is not None
        appearances = json.loads(row[0])
        assert len(appearances) == 2

    def test_connection_count(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_ZIP, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0]
        assert count == 5
