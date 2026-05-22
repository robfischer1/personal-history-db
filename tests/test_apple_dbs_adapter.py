"""Tests for the apple_dbs adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from phdb.adapters.apple_dbs import AppleDbsAdapter
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


class TestAppleDbsIntegration:
    def test_full_ingest(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        adapter = AppleDbsAdapter()
        with connect(apple_db) as conn:
            report = adapter.run(apple_decrypt_dir, conn, apple_settings)

        assert report.rows_inserted == 6
        assert report.rows_skipped == 0

    def test_callhistory_rows(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        adapter = AppleDbsAdapter(only=["callhistory"])
        with connect(apple_db) as conn:
            report = adapter.run(apple_decrypt_dir, conn, apple_settings)
            rows = conn.execute(
                "SELECT schema_type, direction, body_text FROM actions ORDER BY action_key"
            ).fetchall()

        assert report.rows_inserted == 2
        assert rows[0][0] == "Action"
        assert rows[0][1] == "outbound"
        assert "120s" in rows[0][2]

    def test_voicemail_rows(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        adapter = AppleDbsAdapter(only=["voicemail"])
        with connect(apple_db) as conn:
            report = adapter.run(apple_decrypt_dir, conn, apple_settings)
        assert report.rows_inserted == 1

    def test_safari_history_rows(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        adapter = AppleDbsAdapter(only=["safari_history"])
        with connect(apple_db) as conn:
            report = adapter.run(apple_decrypt_dir, conn, apple_settings)
            row = conn.execute(
                "SELECT schema_type, body_text FROM web_pages"
            ).fetchone()
        assert report.rows_inserted == 1
        assert row[0] == "WebPage"
        assert "example.com" in row[1]

    def test_notes_rows(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        adapter = AppleDbsAdapter(only=["notes"])
        with connect(apple_db) as conn:
            report = adapter.run(apple_decrypt_dir, conn, apple_settings)
            row = conn.execute("SELECT subject, body_text FROM digital_documents").fetchone()
        assert report.rows_inserted == 1
        assert row[0] == "My Note"

    def test_idempotent_rerun(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        adapter = AppleDbsAdapter()
        with connect(apple_db) as conn:
            adapter.run(apple_decrypt_dir, conn, apple_settings)

        adapter2 = AppleDbsAdapter()
        with connect(apple_db) as conn:
            r2 = adapter2.run(apple_decrypt_dir, conn, apple_settings)
        assert r2.rows_inserted == 0
        assert r2.rows_yielded == 0

    def test_thread_nodes_created(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        adapter = AppleDbsAdapter(only=["callhistory", "voicemail"])
        with connect(apple_db) as conn:
            report = adapter.run(apple_decrypt_dir, conn, apple_settings)
            thread_nodes = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE kind = 'thread'"
            ).fetchone()[0]
        assert thread_nodes >= 2
        assert report.threads_created >= 2

    def test_time_budget(
        self, apple_db: Path, apple_settings: Settings, apple_decrypt_dir: Path
    ) -> None:
        apple_settings.db_path = apple_db
        adapter = AppleDbsAdapter(max_seconds=0.001)
        with connect(apple_db) as conn:
            report = adapter.run(apple_decrypt_dir, conn, apple_settings)
        assert report.rows_yielded >= 0
