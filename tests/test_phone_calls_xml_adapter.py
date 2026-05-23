"""Tests for the phone_calls_xml plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.formats.smsbr_xml import (
    _epoch_ms_to_iso,
    _normalize_phone,
)
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.phone_calls_xml import PhoneCallsXmlPlugin
from phdb.plugins.phone_calls_xml.ingest import (
    _synthesize_body,
)
from phdb.settings import IdentitySettings, Settings

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "src" / "phdb" / "plugins" / "phone_calls_xml"
FIXTURE_XML = REPO_ROOT / "tests" / "fixtures" / "phone_calls_xml" / "calls.xml"


class TestNormalizePhone:
    def test_with_formatting(self) -> None:
        assert _normalize_phone("+1 (555) 123-4567") == "+15551234567"

    def test_empty(self) -> None:
        assert _normalize_phone("") == ""


class TestEpochMsToIso:
    def test_known(self) -> None:
        result = _epoch_ms_to_iso("1700000000000")
        assert result is not None
        assert "2023" in result

    def test_none(self) -> None:
        assert _epoch_ms_to_iso(None) is None


class TestSynthesizeBody:
    def test_incoming(self) -> None:
        body = _synthesize_body("1", 120, "Jane", "+15551234567")
        assert "incoming" in body
        assert "Jane" in body
        assert "120s" in body

    def test_outgoing(self) -> None:
        body = _synthesize_body("2", 60, "Jane", "+15551234567")
        assert "outgoing" in body

    def test_missed(self) -> None:
        body = _synthesize_body("3", 0, "Bob", "+15559876543")
        assert "Missed" in body
        assert "Bob" in body

    def test_voicemail(self) -> None:
        body = _synthesize_body("4", 45, "(Unknown)", "+15550000000")
        assert "Voicemail" in body
        assert "+15550000000" in body

    def test_rejected(self) -> None:
        body = _synthesize_body("5", 0, "Spam", "+15551111111")
        assert "Rejected" in body

    def test_refused(self) -> None:
        body = _synthesize_body("6", 0, "Name", "+15552222222")
        assert "Refused" in body


@pytest.fixture
def calls_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(
            owner_names={"test user"},
            owner_phones={"+15555555555"},
        ),
    )


@pytest.fixture
def calls_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


def _new_plugin() -> PhoneCallsXmlPlugin:
    """Build a PhoneCallsXmlPlugin with the in-tree manifest."""
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = (PLUGIN_ROOT / "plugin.toml").resolve()
    manifest = load_manifest(manifest_path)
    return PhoneCallsXmlPlugin(manifest)


class TestPhoneCallsXmlIntegration:
    def test_basic_ingest(self, calls_db: Path, calls_settings: Settings) -> None:
        calls_settings.db_path = calls_db
        plugin = _new_plugin()
        with connect(calls_db) as conn:
            report = plugin.run(FIXTURE_XML, conn, calls_settings)
        assert report.rows_inserted == 6
        assert report.rows_skipped == 0

    def test_directions(self, calls_db: Path, calls_settings: Settings) -> None:
        calls_settings.db_path = calls_db
        plugin = _new_plugin()
        with connect(calls_db) as conn:
            plugin.run(FIXTURE_XML, conn, calls_settings)
            rows = conn.execute(
                "SELECT direction FROM actions ORDER BY date_performed"
            ).fetchall()
        dirs = [r[0] for r in rows]
        assert "inbound" in dirs
        assert "outbound" in dirs

    def test_schema_type_action(self, calls_db: Path, calls_settings: Settings) -> None:
        calls_settings.db_path = calls_db
        plugin = _new_plugin()
        with connect(calls_db) as conn:
            plugin.run(FIXTURE_XML, conn, calls_settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM actions").fetchall()
        assert all(t[0] == "Action" for t in types)

    def test_threads_per_number(self, calls_db: Path, calls_settings: Settings) -> None:
        calls_settings.db_path = calls_db
        plugin = _new_plugin()
        with connect(calls_db) as conn:
            plugin.run(FIXTURE_XML, conn, calls_settings)
            threads = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread' ORDER BY label"
            ).fetchall()
        labels = {t[0] for t in threads}
        assert any("calls:+15551234567" in lbl for lbl in labels)

    def test_idempotent_rerun(self, calls_db: Path, calls_settings: Settings) -> None:
        calls_settings.db_path = calls_db
        plugin = _new_plugin()
        with connect(calls_db) as conn:
            plugin.run(FIXTURE_XML, conn, calls_settings)

        plugin2 = _new_plugin()
        with connect(calls_db) as conn:
            r2 = plugin2.run(FIXTURE_XML, conn, calls_settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, calls_db: Path, calls_settings: Settings) -> None:
        calls_settings.db_path = calls_db
        plugin = _new_plugin()
        with connect(calls_db) as conn:
            report = plugin.run(FIXTURE_XML, conn, calls_settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted

    def test_person_facet_projection(self, calls_db: Path, calls_settings: Settings) -> None:
        calls_settings.db_path = calls_db
        plugin = _new_plugin()
        with connect(calls_db) as conn:
            report = plugin.run(FIXTURE_XML, conn, calls_settings)
            # Should have one person-link triple per call (sentTo or receivedFrom)
            sql = """
                SELECT COUNT(*) FROM triples t
                JOIN predicates p ON t.predicate_id = p.id
                WHERE p.name IN ('sentTo', 'receivedFrom')
            """
            person_triples = conn.execute(sql).fetchone()[0]
        assert person_triples == report.rows_inserted

    def test_body_text_content(self, calls_db: Path, calls_settings: Settings) -> None:
        calls_settings.db_path = calls_db
        plugin = _new_plugin()
        with connect(calls_db) as conn:
            plugin.run(FIXTURE_XML, conn, calls_settings)
            bodies = conn.execute("SELECT body_text FROM actions").fetchall()
        for (body,) in bodies:
            assert body is not None
            assert len(body) > 0
