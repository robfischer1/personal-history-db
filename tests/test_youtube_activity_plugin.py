"""Tests for the youtube_activity plugin.

Covers watch-history HTML parsing, search-history HTML parsing,
subscriptions CSV parsing, dedup across activity types, HTML entity
decoding, and full ingest pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.youtube_activity.ingest import (
    YouTubeRecord,
    _extract_channel_id,
    _extract_video_id,
    _parse_timestamp,
    parse_html,
    parse_subscriptions_csv,
)
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_WATCH = FIXTURE_DIR / "youtube_watch_sample.html"
FIXTURE_SEARCH = FIXTURE_DIR / "youtube_search_sample.html"
FIXTURE_SUBS = FIXTURE_DIR / "youtube_subscriptions_sample.csv"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _new_plugin():
    from phdb.core.plugin.manifest import load_manifest
    from phdb.plugins.youtube_activity.plugin import YouTubeActivityPlugin

    manifest_path = Path(
        "src/phdb/plugins/youtube_activity/plugin.toml"
    ).resolve()
    manifest = load_manifest(manifest_path)
    return YouTubeActivityPlugin(manifest)


# ---------------------------------------------------------------------------
# URL extraction helpers
# ---------------------------------------------------------------------------

class TestVideoIdExtraction:
    def test_standard_watch_url(self) -> None:
        assert _extract_video_id(
            "https://www.youtube.com/watch?v=CVlAoOTVnRw"
        ) == "CVlAoOTVnRw"

    def test_watch_url_with_extra_params(self) -> None:
        assert _extract_video_id(
            "https://www.youtube.com/watch?v=abc123&t=120"
        ) == "abc123"

    def test_no_v_param_returns_none(self) -> None:
        assert _extract_video_id(
            "https://www.youtube.com/results?search_query=test"
        ) is None

    def test_empty_url_returns_none(self) -> None:
        assert _extract_video_id("") is None


class TestChannelIdExtraction:
    def test_standard_channel_url(self) -> None:
        assert _extract_channel_id(
            "https://www.youtube.com/channel/UCCgrWW6fsUVdRD7xI52xhnQ"
        ) == "UCCgrWW6fsUVdRD7xI52xhnQ"

    def test_no_channel_path_returns_none(self) -> None:
        assert _extract_channel_id(
            "https://www.youtube.com/watch?v=abc"
        ) is None

    def test_http_channel_url(self) -> None:
        assert _extract_channel_id(
            "http://www.youtube.com/channel/UC-nPM1_kSZf91ZGkcgy_95Q"
        ) == "UC-nPM1_kSZf91ZGkcgy_95Q"


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

class TestTimestampParsing:
    def test_edt_timestamp(self) -> None:
        ts = _parse_timestamp("Mar 8, 2026, 7:47:24 PM EDT")
        assert ts is not None
        # EDT = UTC-4; Mar 8 2026 19:47:24 EDT = Mar 8 2026 23:47:24 UTC
        assert ts == 1773013644

    def test_est_timestamp(self) -> None:
        ts = _parse_timestamp("Mar 7, 2026, 10:30:00 AM EST")
        assert ts is not None
        # EST = UTC-5; Mar 7 2026 10:30:00 EST = Mar 7 2026 15:30:00 UTC
        assert ts == 1772897400

    def test_utc_timestamp(self) -> None:
        ts = _parse_timestamp("Jan 15, 2026, 3:05:12 PM UTC")
        assert ts is not None

    def test_pst_timestamp(self) -> None:
        ts = _parse_timestamp("Feb 28, 2026, 11:59:59 PM PST")
        assert ts is not None

    def test_no_match_returns_none(self) -> None:
        assert _parse_timestamp("no timestamp here") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_timestamp("") is None


# ---------------------------------------------------------------------------
# Watch history HTML parsing
# ---------------------------------------------------------------------------

class TestWatchHistoryParsing:
    def test_parses_correct_count(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        assert len(records) == 4

    def test_all_are_watch_type(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        assert all(r.activity_type == "watch" for r in records)

    def test_video_id_extracted(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        ids = [r.video_id for r in records]
        assert "CVlAoOTVnRw" in ids
        assert "dQw4w9WgXcQ" in ids
        assert "abc123XYZ" in ids

    def test_video_title_extracted(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        titles = [r.title for r in records]
        assert "Never Gonna Give You Up" in titles

    def test_html_entities_decoded_in_title(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        # &#39; should decode to '
        apostrophe_record = next(
            r for r in records if r.video_id == "CVlAoOTVnRw"
        )
        assert "'" in apostrophe_record.title
        assert "&#39;" not in apostrophe_record.title

    def test_html_amp_decoded(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        amp_record = next(
            r for r in records if r.video_id == "abc123XYZ"
        )
        assert "Test & Demo <Video>" == amp_record.title

    def test_channel_name_extracted(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        rick = next(r for r in records if r.video_id == "dQw4w9WgXcQ")
        assert rick.channel == "Rick Astley"

    def test_channel_id_extracted(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        rick = next(r for r in records if r.video_id == "dQw4w9WgXcQ")
        assert rick.channel_id == "UCuAXFkgsw1L7xaCfnd5JJOw"

    def test_timestamp_is_integer(self) -> None:
        records = list(parse_html(FIXTURE_WATCH))
        for r in records:
            assert isinstance(r.timestamp, int)

    def test_no_channel_for_deleted_channel(self) -> None:
        """Entry 4 has no second link (channel deleted)."""
        records = list(parse_html(FIXTURE_WATCH))
        no_channel = next(r for r in records if r.video_id == "LIVE_id_1")
        assert no_channel.channel is None
        assert no_channel.channel_id is None


# ---------------------------------------------------------------------------
# Search history HTML parsing
# ---------------------------------------------------------------------------

class TestSearchHistoryParsing:
    def test_parses_correct_count(self) -> None:
        records = list(parse_html(FIXTURE_SEARCH))
        assert len(records) == 4

    def test_all_are_search_type(self) -> None:
        records = list(parse_html(FIXTURE_SEARCH))
        assert all(r.activity_type == "search" for r in records)

    def test_query_text_extracted(self) -> None:
        records = list(parse_html(FIXTURE_SEARCH))
        titles = [r.title for r in records]
        assert "a good man goes to war" in titles
        assert "python typing tutorial" in titles

    def test_html_amp_decoded_in_query(self) -> None:
        records = list(parse_html(FIXTURE_SEARCH))
        amp = next(r for r in records if "rice" in (r.title or ""))
        assert "how to cook rice & beans" == amp.title

    def test_no_video_id_for_search(self) -> None:
        records = list(parse_html(FIXTURE_SEARCH))
        assert all(r.video_id is None for r in records)

    def test_no_channel_for_search(self) -> None:
        records = list(parse_html(FIXTURE_SEARCH))
        assert all(r.channel is None for r in records)
        assert all(r.channel_id is None for r in records)

    def test_timestamp_present(self) -> None:
        records = list(parse_html(FIXTURE_SEARCH))
        assert all(r.timestamp is not None for r in records)

    def test_search_url_preserved(self) -> None:
        records = list(parse_html(FIXTURE_SEARCH))
        war = next(r for r in records if "good man" in (r.title or ""))
        assert "search_query=" in war.url


# ---------------------------------------------------------------------------
# Subscriptions CSV parsing
# ---------------------------------------------------------------------------

class TestSubscriptionsCsvParsing:
    def test_parses_correct_count(self) -> None:
        records = list(parse_subscriptions_csv(FIXTURE_SUBS))
        assert len(records) == 5

    def test_all_are_subscribe_type(self) -> None:
        records = list(parse_subscriptions_csv(FIXTURE_SUBS))
        assert all(r.activity_type == "subscribe" for r in records)

    def test_channel_title_extracted(self) -> None:
        records = list(parse_subscriptions_csv(FIXTURE_SUBS))
        titles = [r.title for r in records]
        assert "How to ADHD" in titles
        assert "Rick Astley" in titles

    def test_channel_id_extracted(self) -> None:
        records = list(parse_subscriptions_csv(FIXTURE_SUBS))
        adhd = next(r for r in records if r.title == "How to ADHD")
        assert adhd.channel_id == "UC-nPM1_kSZf91ZGkcgy_95Q"

    def test_no_timestamp_for_subscriptions(self) -> None:
        records = list(parse_subscriptions_csv(FIXTURE_SUBS))
        assert all(r.timestamp is None for r in records)

    def test_channel_url_preserved(self) -> None:
        records = list(parse_subscriptions_csv(FIXTURE_SUBS))
        adhd = next(r for r in records if r.title == "How to ADHD")
        assert "youtube.com/channel/" in adhd.url

    def test_ampersand_in_channel_title(self) -> None:
        records = list(parse_subscriptions_csv(FIXTURE_SUBS))
        amp = next(r for r in records if "Test Channel" in (r.title or ""))
        assert amp.title == "Test Channel & More"


# ---------------------------------------------------------------------------
# Dedup across activity types (DB-level)
# ---------------------------------------------------------------------------

class TestDedup:
    def test_watch_dedup_by_video_id_and_timestamp(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report1 = plugin.run(FIXTURE_WATCH, conn, settings)
        with connect(db_path) as conn:
            report2 = plugin.run(FIXTURE_WATCH, conn, settings)
        # Second run should skip all — duplicates
        assert report2.rows_skipped == report2.rows_yielded
        assert report2.rows_inserted == 0

    def test_search_dedup_by_title_and_timestamp(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_SEARCH, conn, settings)
        with connect(db_path) as conn:
            report2 = plugin.run(FIXTURE_SEARCH, conn, settings)
        assert report2.rows_skipped == report2.rows_yielded
        assert report2.rows_inserted == 0

    def test_subscribe_dedup_by_channel_id(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_SUBS, conn, settings)
        with connect(db_path) as conn:
            report2 = plugin.run(FIXTURE_SUBS, conn, settings)
        assert report2.rows_skipped == report2.rows_yielded
        assert report2.rows_inserted == 0

    def test_different_types_not_deduped(self, tmp_path: Path) -> None:
        """Watch and search land in separate canonical tables and both persist."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_WATCH, conn, settings)
            plugin.run(FIXTURE_SEARCH, conn, settings)
            watch_count = conn.execute(
                "SELECT COUNT(*) FROM watch_actions"
            ).fetchone()[0]
            search_count = conn.execute(
                "SELECT COUNT(*) FROM search_actions"
            ).fetchone()[0]
        assert watch_count == 4
        assert search_count == 4


# ---------------------------------------------------------------------------
# Full ingest pipeline
# ---------------------------------------------------------------------------

class TestFullIngest:
    def test_watch_rows_inserted(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_WATCH, conn, settings)
        assert report.rows_yielded == 4
        assert report.rows_inserted == 4

    def test_search_rows_inserted(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_SEARCH, conn, settings)
        assert report.rows_yielded == 4
        assert report.rows_inserted == 4

    def test_subscriptions_rows_inserted(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_SUBS, conn, settings)
        assert report.rows_yielded == 5
        assert report.rows_inserted == 5

    def test_db_row_contents_watch(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_WATCH, conn, settings)
            row = conn.execute(
                """SELECT wa.*, wp.url AS web_url
                   FROM watch_actions wa
                   LEFT JOIN web_pages wp ON wp.id = wa.web_page_id
                   WHERE wa.watch_key = ?""",
                ("dQw4w9WgXcQ",),
            ).fetchone()
        assert row is not None
        assert row["schema_type"] == "WatchAction"
        assert row["platform_name"] == "YouTube"
        assert row["subject"] == "Never Gonna Give You Up"
        assert row["date_watched"] is not None
        assert row["web_page_id"] is not None
        assert "dQw4w9WgXcQ" in row["web_url"]

    def test_db_row_contents_search(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_SEARCH, conn, settings)
            row = conn.execute(
                """SELECT sa.*, wp.url AS web_url
                   FROM search_actions sa
                   LEFT JOIN web_pages wp ON wp.id = sa.web_page_id
                   WHERE sa.subject = ?""",
                ("a good man goes to war",),
            ).fetchone()
        assert row is not None
        assert row["schema_type"] == "SearchAction"
        assert row["sender_name"] == "YouTube"
        assert row["date_performed"] is not None
        assert row["web_page_id"] is not None
        assert "search_query=" in row["web_url"]

    def test_db_row_contents_subscribe(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_SUBS, conn, settings)
            row = conn.execute(
                """SELECT fa.*, wp.url AS web_url
                   FROM follow_actions fa
                   LEFT JOIN web_pages wp ON wp.id = fa.web_page_id
                   WHERE fa.follow_key = ?""",
                ("UC-nPM1_kSZf91ZGkcgy_95Q",),
            ).fetchone()
        assert row is not None
        assert row["schema_type"] == "FollowAction"
        assert row["platform_name"] == "YouTube"
        assert row["channel_name"] == "How to ADHD"
        assert row["subject"] == "How to ADHD"
        assert row["date_followed"] is None
        assert row["web_page_id"] is not None
        assert "youtube.com/channel/" in row["web_url"]

    def test_source_file_registered(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_WATCH, conn, settings)
            sf = conn.execute(
                "SELECT source_kind, file_kind FROM source_files "
                "WHERE source_kind = 'youtube-activity'"
            ).fetchone()
        assert sf is not None
        assert sf["source_kind"] == "youtube-activity"
        assert sf["file_kind"] == "html"

    def test_source_file_csv_registered(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_SUBS, conn, settings)
            sf = conn.execute(
                "SELECT source_kind, file_kind FROM source_files "
                "WHERE source_kind = 'youtube-activity'"
            ).fetchone()
        assert sf is not None
        assert sf["file_kind"] == "csv"

    def test_directory_ingest(self, tmp_path: Path) -> None:
        """When given a directory with multiple files, ingests all."""
        # Set up a directory with copies of fixture files
        yt_dir = tmp_path / "YouTube and YouTube Music" / "history"
        yt_dir.mkdir(parents=True)
        import shutil
        shutil.copy(FIXTURE_WATCH, yt_dir / "watch-history.html")
        shutil.copy(FIXTURE_SEARCH, yt_dir / "search-history.html")

        subs_dir = tmp_path / "YouTube and YouTube Music" / "subscriptions"
        subs_dir.mkdir(parents=True)
        shutil.copy(FIXTURE_SUBS, subs_dir / "subscriptions.csv")

        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(tmp_path, conn, settings)
        # 4 watch + 4 search + 5 subscribe = 13
        assert report.rows_inserted == 13
