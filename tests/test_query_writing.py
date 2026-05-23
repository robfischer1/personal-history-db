"""Tests for the writing delta-stream query functions in phdb.query."""

from __future__ import annotations

from pathlib import Path

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.writing_deltas import WritingDeltasPlugin
from phdb.query import writing_arc, writing_session_detail, writing_stats
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "writing_deltas"
FIXTURE_BASIC = FIXTURE_DIR / "basic_session.ndjson"
FIXTURE_DAY1 = FIXTURE_DIR / "day1_open.ndjson"
FIXTURE_DAY2 = FIXTURE_DIR / "day2_close.ndjson"


def _setup_and_ingest(tmp_path: Path, *fixtures: Path) -> Path:
    """Build a temp DB, apply migrations, ingest each fixture in order."""
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    adapter = WritingDeltasPlugin()
    for fixture in fixtures:
        with connect(db_path) as conn:
            adapter.run(fixture, conn, settings)
    return db_path


class TestWritingArc:
    def test_returns_session_for_known_note(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)
        with connect(db_path) as conn:
            result = writing_arc(conn, "Notes/test.md")
        assert result["note_path"] == "Notes/test.md"
        assert result["session_count"] == 1
        assert len(result["sessions"]) == 1
        s = result["sessions"][0]
        assert s["session_id"] == "sess_test_1"
        assert s["started_at"] == 1_000_000_000_100
        assert s["ended_at"] == 1_000_000_000_800
        assert s["duration_ms"] == 700
        assert s["doc_change_count"] == 5
        assert s["selection_change_count"] == 1
        assert s["undo_count"] == 1
        assert s["paste_count"] == 1
        # 2 deleted / 12 inserted = 0.167
        assert s["rewrite_ratio"] == round(2 / 12, 3)

    def test_empty_result_for_unknown_note(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)
        with connect(db_path) as conn:
            result = writing_arc(conn, "Notes/no-such-file.md")
        assert result["note_path"] == "Notes/no-such-file.md"
        assert result["session_count"] == 0
        assert result["sessions"] == []

    def test_orders_sessions_most_recent_first(self, tmp_path: Path) -> None:
        # Day 1 fixture ingested first creates a session at ts=1_000_000_002_000.
        # Day 2 fixture adds a session-end + another doc-change. Same session_id,
        # so still just one session. Use a synthetic scenario instead.
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)

        # Manually insert a second, later session for the same note path.
        with connect(db_path) as conn:
            conn.execute(
                """INSERT INTO writing_sessions
                   (session_id, note_path, started_at, ended_at, ended_reason)
                   VALUES (?, ?, ?, ?, ?)""",
                ("sess_later", "Notes/test.md", 2_000_000_000_000, 2_000_000_001_000, "blur"),
            )
            conn.commit()
            result = writing_arc(conn, "Notes/test.md")

        assert result["session_count"] == 2
        # Most recent first
        assert result["sessions"][0]["session_id"] == "sess_later"
        assert result["sessions"][1]["session_id"] == "sess_test_1"

    def test_rewrite_ratio_zero_when_no_inserts(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path)
        with connect(db_path) as conn:
            conn.execute(
                """INSERT INTO writing_sessions
                   (session_id, note_path, started_at,
                    insert_count, delete_count,
                    total_inserted_chars, total_deleted_chars)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("sess_zero", "Notes/zero.md", 1_000, 0, 0, 0, 0),
            )
            conn.commit()
            result = writing_arc(conn, "Notes/zero.md")
        assert result["sessions"][0]["rewrite_ratio"] == 0.0


class TestWritingSessionDetail:
    def test_returns_full_session_with_samples(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)
        with connect(db_path) as conn:
            result = writing_session_detail(conn, "sess_test_1")

        assert result["session_id"] == "sess_test_1"
        assert result["note_path"] == "Notes/test.md"
        assert result["duration_ms"] == 700
        assert result["aggregates"]["doc_change_count"] == 5
        assert result["aggregates"]["undo_count"] == 1
        assert result["aggregates"]["paste_count"] == 1

        # 6 total events — first_events = first 10 capped, so all 6.
        assert len(result["first_events"]) == 6
        # First event in fixture is the 'hello' type
        assert result["first_events"][0]["user_event"] == "input.type"
        assert result["first_events"][0]["inserted_text"] == "hello"

        # reversals = every undo + paste event, in order
        assert len(result["reversals"]) == 2
        events_in_order = [(e["user_event"], e["inserted_text"], e["deleted_text"])
                           for e in result["reversals"]]
        assert events_in_order == [
            ("input.paste", "!", ""),
            ("undo", "", "!"),
        ]

    def test_unknown_session_returns_error(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)
        with connect(db_path) as conn:
            result = writing_session_detail(conn, "sess_nope")
        assert "error" in result
        assert "sess_nope" in result["error"]

    def test_delta_sample_size_limits_first_and_last(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)
        with connect(db_path) as conn:
            result = writing_session_detail(conn, "sess_test_1", delta_sample_size=2)
        assert len(result["first_events"]) == 2
        assert len(result["last_events"]) == 2
        # last_events should be the actual last two in chronological order:
        # session has 6 events; last two = selection-change at 700ms, then session-end is not a delta.
        # The two last deltas are undo at 600 and selection-change at 700.
        last_user_events = [e["user_event"] for e in result["last_events"]]
        assert last_user_events == ["undo", None]


class TestWritingStats:
    def test_empty_db_returns_zeros(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path)
        with connect(db_path) as conn:
            result = writing_stats(conn)
        assert result["session_count"] == 0
        assert result["notes_touched"] == 0
        assert result["total_inserted_chars"] == 0
        assert result["total_deleted_chars"] == 0
        assert result["rewrite_ratio"] == 0.0
        assert result["top_notes"] == []

    def test_aggregates_match_basic_fixture(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)
        with connect(db_path) as conn:
            result = writing_stats(conn)
        assert result["session_count"] == 1
        assert result["notes_touched"] == 1
        assert result["total_doc_changes"] == 5
        assert result["total_selection_changes"] == 1
        assert result["total_inserts"] == 3
        assert result["total_deletes"] == 2
        assert result["total_inserted_chars"] == 12
        assert result["total_deleted_chars"] == 2
        assert result["total_undos"] == 1
        assert result["total_pastes"] == 1
        assert result["rewrite_ratio"] == round(2 / 12, 3)
        assert len(result["top_notes"]) == 1
        assert result["top_notes"][0]["note_path"] == "Notes/test.md"

    def test_note_path_filter(self, tmp_path: Path) -> None:
        # Ingest two distinct sessions on two distinct notes.
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC, FIXTURE_DAY1)
        with connect(db_path) as conn:
            all_stats = writing_stats(conn)
            assert all_stats["notes_touched"] == 2
            assert all_stats["session_count"] == 2

            filtered = writing_stats(conn, note_path="Notes/cross.md")
            assert filtered["notes_touched"] == 1
            assert filtered["session_count"] == 1
            assert filtered["top_notes"][0]["note_path"] == "Notes/cross.md"

    def test_since_filter_excludes_earlier_sessions(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)
        with connect(db_path) as conn:
            # Fixture session is at epoch ms 1_000_000_000_100 == 2001-09-09 UTC.
            # Filter from 2002 onward excludes it.
            future = writing_stats(conn, since="2002-01-01")
            past = writing_stats(conn, since="2001-01-01")
        assert future["session_count"] == 0
        assert past["session_count"] == 1

    def test_invalid_date_filter_is_ignored(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path, FIXTURE_BASIC)
        with connect(db_path) as conn:
            result = writing_stats(conn, since="not-a-date")
        # Falls through with no filter applied; returns the basic-fixture totals.
        assert result["session_count"] == 1

    def test_top_n_limits_results(self, tmp_path: Path) -> None:
        db_path = _setup_and_ingest(tmp_path)
        with connect(db_path) as conn:
            for i in range(5):
                conn.execute(
                    """INSERT INTO writing_sessions
                       (session_id, note_path, started_at, doc_change_count)
                       VALUES (?, ?, ?, ?)""",
                    (f"sess_{i}", f"Notes/n{i}.md", 1000 + i, 10 - i),
                )
            conn.commit()
            result = writing_stats(conn, top_n=3)
        assert len(result["top_notes"]) == 3
        # Sorted by total_doc_changes DESC
        assert [n["note_path"] for n in result["top_notes"]] == [
            "Notes/n0.md", "Notes/n1.md", "Notes/n2.md",
        ]
