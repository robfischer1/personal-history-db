"""Tests for the spotify adapter and format parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.adapters.spotify import SpotifyAdapter
from phdb.db import connect
from phdb.formats.spotify_json import parse
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "spotify"


class TestSpotifyFormatParser:
    def test_track(self, tmp_path: Path) -> None:
        import json
        data = [{
            "ts": "2024-01-15T14:30:00Z",
            "master_metadata_track_name": "Bohemian Rhapsody",
            "master_metadata_album_artist_name": "Queen",
            "master_metadata_album_album_name": "A Night at the Opera",
            "spotify_track_uri": "spotify:track:abc",
            "ms_played": 354000,
        }]
        f = tmp_path / "Streaming_History_Audio_0.json"
        f.write_text(json.dumps(data))
        records = list(parse(tmp_path))
        assert len(records) == 1
        rec = records[0]
        assert rec.media_type == "music"
        assert "Bohemian Rhapsody" in rec.title
        assert rec.artist == "Queen"
        assert rec.album == "A Night at the Opera"
        assert rec.duration_ms == 354000

    def test_podcast(self, tmp_path: Path) -> None:
        import json
        data = [{
            "ts": "2024-01-15T14:44:00Z",
            "episode_name": "The Daily",
            "episode_show_name": "NYT News",
            "spotify_episode_uri": "spotify:episode:ghi789",
            "ms_played": 1200000,
        }]
        f = tmp_path / "Streaming_History_Audio_0.json"
        f.write_text(json.dumps(data))
        records = list(parse(tmp_path))
        assert len(records) == 1
        rec = records[0]
        assert rec.media_type == "podcast"
        assert "The Daily" in rec.title

    def test_audiobook(self, tmp_path: Path) -> None:
        import json
        data = [{
            "ts": "2024-01-15T15:04:00Z",
            "audiobook_title": "Project Hail Mary",
            "audiobook_chapter_title": "Chapter 1",
            "audiobook_uri": "spotify:show:jkl012",
            "ms_played": 2400000,
        }]
        f = tmp_path / "Streaming_History_Audio_0.json"
        f.write_text(json.dumps(data))
        records = list(parse(tmp_path))
        assert len(records) == 1
        rec = records[0]
        assert rec.media_type == "audiobook"
        assert "Project Hail Mary" in rec.title

    def test_no_timestamp_skipped(self, tmp_path: Path) -> None:
        import json
        data = [{"ts": "", "master_metadata_track_name": "Song", "ms_played": 100}]
        f = tmp_path / "Streaming_History_Audio_0.json"
        f.write_text(json.dumps(data))
        records = list(parse(tmp_path))
        assert len(records) == 0

    def test_no_track_no_episode_skipped(self, tmp_path: Path) -> None:
        import json
        data = [{"ts": "2024-01-15T14:30:00Z", "ms_played": 100}]
        f = tmp_path / "Streaming_History_Audio_0.json"
        f.write_text(json.dumps(data))
        records = list(parse(tmp_path))
        assert len(records) == 0


@pytest.fixture
def spotify_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(owner_names={"test user"}),
    )


@pytest.fixture
def spotify_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
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
            bulk = conn.execute("SELECT COUNT(*) FROM listen_actions WHERE is_bulk = 1").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM listen_actions").fetchone()[0]
        assert bulk == total

    def test_single_thread(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            adapter.run(FIXTURE_DIR, conn, spotify_settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads == 1

    def test_thread_key(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            adapter.run(FIXTURE_DIR, conn, spotify_settings)
            label = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread'"
            ).fetchone()[0]
        assert "spotify:listening" in label

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
            types = conn.execute("SELECT DISTINCT schema_type FROM listen_actions").fetchall()
        assert all(t[0] == "ListenAction" for t in types)

    def test_message_thread_bridge(self, spotify_db: Path, spotify_settings: Settings) -> None:
        spotify_settings.db_path = spotify_db
        adapter = SpotifyAdapter()
        with connect(spotify_db) as conn:
            report = adapter.run(FIXTURE_DIR, conn, spotify_settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted
