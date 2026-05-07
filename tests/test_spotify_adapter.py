"""Tests for the spotify adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.adapters.spotify import SpotifyAdapter, _parse_event
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "spotify"


class TestParseEvent:
    def test_track(self) -> None:
        evt = {
            "ts": "2024-01-15T14:30:00Z",
            "master_metadata_track_name": "Bohemian Rhapsody",
            "master_metadata_album_artist_name": "Queen",
            "master_metadata_album_album_name": "A Night at the Opera",
            "spotify_track_uri": "spotify:track:abc",
            "ms_played": 354000,
        }
        row = _parse_event(evt, 0, 0)
        assert row is not None
        assert row.schema_type == "ListenAction"
        assert "Bohemian Rhapsody" in row.subject
        assert "Queen" in row.subject
        assert row.is_bulk == 1

    def test_podcast(self) -> None:
        evt = {
            "ts": "2024-01-15T14:44:00Z",
            "episode_name": "The Daily",
            "episode_show_name": "NYT News",
            "spotify_episode_uri": "spotify:episode:ghi789",
            "ms_played": 1200000,
        }
        row = _parse_event(evt, 0, 2)
        assert row is not None
        assert "The Daily" in row.subject
        assert "Podcast" in row.body_text

    def test_audiobook(self) -> None:
        evt = {
            "ts": "2024-01-15T15:04:00Z",
            "audiobook_title": "Project Hail Mary",
            "audiobook_chapter_title": "Chapter 1",
            "audiobook_uri": "spotify:show:jkl012",
            "ms_played": 2400000,
        }
        row = _parse_event(evt, 0, 3)
        assert row is not None
        assert "Project Hail Mary" in row.subject
        assert "Audiobook" in row.body_text

    def test_no_timestamp_skipped(self) -> None:
        evt = {"ts": "", "master_metadata_track_name": "Song", "ms_played": 100}
        row = _parse_event(evt, 0, 4)
        assert row is None

    def test_no_track_no_episode_skipped(self) -> None:
        evt = {"ts": "2024-01-15T14:30:00Z", "ms_played": 100}
        row = _parse_event(evt, 0, 5)
        assert row is None


@pytest.fixture
def spotify_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(owner_names={"test user"}),
    )


@pytest.fixture
def spotify_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestSpotifyIntegration:
    def test_basic_ingest(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            report = adapter.run(FIXTURE_DIR, conn, spotify_settings)
        assert report.rows_inserted == 4
        assert report.rows_skipped == 0

    def test_all_bulk(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            adapter.run(FIXTURE_DIR, conn, spotify_settings)
            bulk = conn.execute("SELECT COUNT(*) FROM messages WHERE is_bulk = 1").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert bulk == total

    def test_single_thread(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            adapter.run(FIXTURE_DIR, conn, spotify_settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads == 1

    def test_thread_key(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            adapter.run(FIXTURE_DIR, conn, spotify_settings)
            key = conn.execute("SELECT thread_key FROM threads").fetchone()[0]
        assert key == "spotify:listening"

    def test_idempotent_rerun(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            adapter.run(FIXTURE_DIR, conn, spotify_settings)

        adapter2 = SpotifyAdapter()
        with connect(spotify_db) as conn:
            r2 = adapter2.run(FIXTURE_DIR, conn, spotify_settings)
        assert r2.rows_inserted == 0

    def test_time_budget(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter(max_seconds=0.001)
        with connect(spotify_db) as conn:
            report = adapter.run(FIXTURE_DIR, conn, spotify_settings)
        assert report.rows_yielded >= 0

    def test_schema_type(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            adapter.run(FIXTURE_DIR, conn, spotify_settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "ListenAction" for t in types)

    def test_message_thread_bridge(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            report = adapter.run(FIXTURE_DIR, conn, spotify_settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
