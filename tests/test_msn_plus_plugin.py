"""Tests for the msn_plus plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.core.plugin.manifest import PluginManifest
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.msn_plus.plugin import MsnPlusPlugin
from phdb.settings import IdentitySettings, Settings

FIXTURES = Path(__file__).parent / "fixtures" / "chat_logs"


@pytest.fixture
def msn_plus_plugin() -> MsnPlusPlugin:
    manifest = PluginManifest(
        name="msn_plus",
        version="0.1.0",
        description="test",
        kind="source",
        entry_point="phdb.plugins.msn_plus.plugin:MsnPlusPlugin",
    )
    return MsnPlusPlugin(manifest)


@pytest.fixture
def msn_plus_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(
            owner_names={"testowner"},
            owner_emails={"testowner@example.com"},
            owner_phones=set(),
            owner_handles={"msn": {"testowner@example.com"}},
        ),
    )


@pytest.fixture
def msn_plus_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestMsnPlusDiscover:
    def test_discovers_msn_plus_files(self, msn_plus_plugin: MsnPlusPlugin) -> None:
        msn_plus_dir = FIXTURES / "MSN_Plus"
        found = list(msn_plus_plugin.discover(msn_plus_dir))
        assert len(found) >= 1
        paths = [p for p, _kind in found]
        assert any("msn_plus_chat.txt" in str(p) for p in paths)

    def test_skips_non_msn_plus(self, msn_plus_plugin: MsnPlusPlugin) -> None:
        aim_dir = FIXTURES / "AIM"
        found = list(msn_plus_plugin.discover(aim_dir))
        assert len(found) == 0

    def test_source_kind(self, msn_plus_plugin: MsnPlusPlugin) -> None:
        msn_plus_dir = FIXTURES / "MSN_Plus"
        found = list(msn_plus_plugin.discover(msn_plus_dir))
        for _path, kind in found:
            assert kind == "msn-plus"


class TestMsnPlusPluginIntegration:
    def test_basic_ingest(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            report = msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
        assert report.rows_inserted > 0

    def test_source_kind_in_db(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
            kinds = conn.execute(
                "SELECT DISTINCT source_kind FROM source_files WHERE source_kind = 'msn-plus'"
            ).fetchall()
        assert len(kinds) == 1

    def test_body_text_source(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
            sources = conn.execute(
                "SELECT DISTINCT body_text_source FROM chat_messages WHERE body_text_source = 'msn-plus-log'"
            ).fetchall()
        assert len(sources) == 1

    def test_direction_inference(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
            outbound = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction = 'outbound' AND body_text_source = 'msn-plus-log'"
            ).fetchone()[0]
            inbound = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction = 'inbound' AND body_text_source = 'msn-plus-log'"
            ).fetchone()[0]
        assert outbound > 0
        assert inbound > 0

    def test_threads_created(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
            threads = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread' AND label LIKE '%msn:%'"
            ).fetchall()
        assert len(threads) >= 2

    def test_recipients_recorded(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
            sent_to = conn.execute(
                "SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'sentTo'"
            ).fetchone()[0]
        assert sent_to > 0

    def test_idempotent_rerun(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
        with connect(msn_plus_db) as conn:
            r2 = msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
        assert r2.rows_inserted == 0

    def test_per_file_source_registration(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
            sf_count = conn.execute(
                "SELECT COUNT(*) FROM source_files WHERE source_kind = 'msn-plus'"
            ).fetchone()[0]
        assert sf_count >= 1

    def test_time_budget(
        self, msn_plus_db: Path, msn_plus_settings: Settings,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        manifest = PluginManifest(
            name="msn_plus",
            version="0.1.0",
            description="test",
            kind="source",
            entry_point="phdb.plugins.msn_plus.plugin:MsnPlusPlugin",
        )
        plugin = MsnPlusPlugin(manifest, max_seconds=0.001)
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            report = plugin.run(msn_plus_dir, conn, msn_plus_settings)
        assert report.rows_yielded >= 0

    def test_message_thread_bridge(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
            bridges = conn.execute(
                "SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id "
                "WHERE p.name = 'inThread'"
            ).fetchone()[0]
            msgs = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE body_text_source = 'msn-plus-log'"
            ).fetchone()[0]
        assert bridges == msgs

    def test_no_header_lines_ingested(
        self, msn_plus_db: Path, msn_plus_settings: Settings, msn_plus_plugin: MsnPlusPlugin,
    ) -> None:
        msn_plus_settings.db_path = msn_plus_db
        msn_plus_dir = FIXTURES / "MSN_Plus"
        with connect(msn_plus_db) as conn:
            msn_plus_plugin.run(msn_plus_dir, conn, msn_plus_settings)
            bad = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE sender_name LIKE '| %' AND body_text_source = 'msn-plus-log'"
            ).fetchone()[0]
        assert bad == 0
