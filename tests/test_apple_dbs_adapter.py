"""Tests for the apple_dbs plugin (Phase 7 port)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from phdb.plugins.apple_dbs import AppleDbsPlugin
from phdb.formats.apple_dbs_sqlite import apple_ts_to_iso as _apple_ts_to_iso
from phdb.formats.apple_dbs_sqlite import normalize_phone as _normalize_phone
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings


class TestAppleTsToIso:
    def test_zero(self) -> None:
        assert _apple_ts_to_iso(0) == "2001-01-01T00:00:00"

    def test_known(self) -> None:
        result = _apple_ts_to_iso(700_000_000)
        assert result is not None
        assert "2023" in result

    def test_none(self) -> None:
        assert _apple_ts_to_iso(None) is None


class TestNormalizePhone:
    def test_digits_only(self) -> None:
        assert _normalize_phone("(201) 555-0100") == "+12015550100"

    def test_international(self) -> None:
        assert _normalize_phone("+442071234567") == "+442071234567"

    def test_empty(self) -> None:
        assert _normalize_phone("") == ""


@pytest.fixture
def apple_decrypt_dir(tmp_path: Path) -> Path:
    """Create synthetic Apple backup SQLite databases."""
    # CallHistory
    ch_dir = tmp_path / "callhistory"
    ch_dir.mkdir()
    ch_db = ch_dir / "CallHistory.storedata"
    conn = sqlite3.connect(str(ch_db))
    conn.execute("""CREATE TABLE ZCALLRECORD (
        Z_PK INTEGER PRIMARY KEY, ZADDRESS TEXT, ZDATE REAL,
        ZDURATION REAL, ZORIGINATED INTEGER, ZANSWERED INTEGER
    )""")
    conn.execute(
        "INSERT INTO ZCALLRECORD VALUES (1, '+15551234567', 700000000, 120, 1, 1)"
    )
    conn.execute(
        "INSERT INTO ZCALLRECORD VALUES (2, '+15559876543', 700001000, 0, 0, 0)"
    )
    conn.commit()
    conn.close()

    # Voicemail
    vm_dir = tmp_path / "voicemail"
    vm_dir.mkdir()
    vm_db = vm_dir / "voicemail.db"
    conn = sqlite3.connect(str(vm_db))
    conn.execute("""CREATE TABLE voicemail (
        ROWID INTEGER PRIMARY KEY, sender TEXT, date INTEGER,
        duration INTEGER, callback_num TEXT, trashed_date INTEGER
    )""")
    conn.execute("INSERT INTO voicemail VALUES (1, '+15551234567', 1700000000, 30, NULL, NULL)")
    conn.commit()
    conn.close()

    # Safari History
    sh_dir = tmp_path / "safari_history"
    sh_dir.mkdir()
    sh_db = sh_dir / "History.db"
    conn = sqlite3.connect(str(sh_db))
    conn.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute("CREATE TABLE history_visits (history_item INTEGER, title TEXT, visit_time REAL)")
    conn.execute("INSERT INTO history_items VALUES (1, 'https://example.com')")
    conn.execute("INSERT INTO history_visits VALUES (1, 'Example Site', 700000000)")
    conn.commit()
    conn.close()

    # Safari Bookmarks
    sb_dir = tmp_path / "safari_bookmarks"
    sb_dir.mkdir()
    sb_db = sb_dir / "Bookmarks.db"
    conn = sqlite3.connect(str(sb_db))
    conn.execute("CREATE TABLE bookmarks (title TEXT, url TEXT)")
    conn.execute("INSERT INTO bookmarks VALUES ('GitHub', 'https://github.com')")
    conn.commit()
    conn.close()

    # Notes (modern schema)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    notes_db = notes_dir / "NoteStore.sqlite"
    conn = sqlite3.connect(str(notes_db))
    conn.execute("""CREATE TABLE ZICCLOUDSYNCINGOBJECT (
        Z_PK INTEGER PRIMARY KEY, ZTITLE1 TEXT, ZSNIPPET TEXT,
        ZCREATIONDATE1 REAL, ZMODIFICATIONDATE1 REAL, ZFOLDER INTEGER,
        ZTITLE2 TEXT
    )""")
    conn.execute(
        "INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES (1, 'My Note', 'Some snippet text', 700000000, 700001000, NULL, NULL)"
    )
    conn.commit()
    conn.close()

    # iMessage (chat.db)
    im_dir = tmp_path / "imessage"
    im_dir.mkdir()
    im_db = im_dir / "chat.db"
    conn = sqlite3.connect(str(im_db))
    conn.execute("""CREATE TABLE message (
        ROWID INTEGER PRIMARY KEY, text TEXT, date INTEGER,
        handle_id INTEGER, is_from_me INTEGER, cache_has_attachments INTEGER
    )""")
    conn.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
    conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT)")
    conn.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
    conn.execute("INSERT INTO message VALUES (1, 'Hello from iMessage', 700000000000000000, 1, 0, 0)")
    conn.execute("INSERT INTO handle VALUES (1, '+15551112222')")
    conn.execute("INSERT INTO chat VALUES (1, 'chat123')")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 1)")
    conn.commit()
    conn.close()

    return tmp_path


@pytest.fixture
def apple_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(
            owner_names={"test user"},
            owner_phones={"+15555555555"},
        ),
    )


@pytest.fixture
def apple_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestAppleDbsPluginIntegration:
    def test_full_ingest(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        # We need a manifest to initialize the plugin
        from phdb.core.plugin.manifest import load_manifest
        manifest = load_manifest(Path("src/phdb/plugins/apple_dbs/plugin.toml"))
        plugin = AppleDbsPlugin(manifest)
        
        with connect(apple_db) as conn:
            summary = plugin.run(apple_decrypt_dir, conn, apple_settings)

        # 2 calls + 1 vm + 1 safari hist + 1 safari bm + 1 note + 1 imessage = 7
        assert summary.rows_inserted == 7
        assert summary.rows_skipped == 0

    def test_callhistory_rows(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        from phdb.core.plugin.manifest import load_manifest
        manifest = load_manifest(Path("src/phdb/plugins/apple_dbs/plugin.toml"))
        plugin = AppleDbsPlugin(manifest)
        
        with connect(apple_db) as conn:
            summary = plugin.run(apple_decrypt_dir, conn, apple_settings, only=["callhistory"])
            rows = conn.execute(
                "SELECT schema_type, direction, body_text FROM actions ORDER BY id"
            ).fetchall()

        assert summary.rows_inserted == 2
        assert rows[0][0] == "Action"
        assert rows[0][1] == "outbound"
        assert "120s" in rows[0][2]

    def test_safari_history_rows(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        from phdb.core.plugin.manifest import load_manifest
        manifest = load_manifest(Path("src/phdb/plugins/apple_dbs/plugin.toml"))
        plugin = AppleDbsPlugin(manifest)

        with connect(apple_db) as conn:
            summary = plugin.run(apple_decrypt_dir, conn, apple_settings, only=["safari_history"])
            wp = conn.execute(
                "SELECT normalized_url, title, domain FROM web_pages"
            ).fetchone()
            # New assertion for BrowseAction (Phase 7 requirement)
            browse = conn.execute(
                "SELECT web_page_id, visit_time FROM browse_actions"
            ).fetchone()

        assert summary.rows_inserted == 1
        assert wp is not None
        assert "example.com" in wp[0]
        assert browse is not None
        assert browse[0] == 1
        assert "2023" in browse[1]

    def test_imessage_rows(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        from phdb.core.plugin.manifest import load_manifest
        manifest = load_manifest(Path("src/phdb/plugins/apple_dbs/plugin.toml"))
        plugin = AppleDbsPlugin(manifest)

        with connect(apple_db) as conn:
            summary = plugin.run(apple_decrypt_dir, conn, apple_settings, only=["imessage"])
            row = conn.execute(
                "SELECT sender_address, body_text FROM chat_messages"
            ).fetchone()

        assert summary.rows_inserted == 1
        assert row is not None
        assert row[0] == "+15551112222"
        assert "Hello from iMessage" in row[1]

    def test_idempotent_rerun(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        from phdb.core.plugin.manifest import load_manifest
        manifest = load_manifest(Path("src/phdb/plugins/apple_dbs/plugin.toml"))
        plugin = AppleDbsPlugin(manifest)
        
        with connect(apple_db) as conn:
            plugin.run(apple_decrypt_dir, conn, apple_settings)

        with connect(apple_db) as conn:
            r2 = plugin.run(apple_decrypt_dir, conn, apple_settings)
        assert r2.rows_inserted == 0
        assert r2.rows_yielded == 0
