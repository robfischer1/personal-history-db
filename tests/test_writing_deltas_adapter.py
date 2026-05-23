"""Tests for the writing-deltas adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.plugins.writing_deltas import WritingDeltasPlugin
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "writing_deltas"
FIXTURE_BASIC = FIXTURE_DIR / "basic_session.ndjson"
FIXTURE_PARTIAL = FIXTURE_DIR / "partial_line.ndjson"
FIXTURE_DAY1 = FIXTURE_DIR / "day1_open.ndjson"
FIXTURE_DAY2 = FIXTURE_DIR / "day2_close.ndjson"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestBasicIngest:
    def test_one_session_with_full_aggregates(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = WritingDeltasPlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_BASIC, conn, settings)

        # The fixture has 5 doc-change + 1 selection-change events (note-switch
        # is intentionally skipped from materialisation).
        assert report.threads_created == 1
        assert report.rows_yielded == 6
        assert report.rows_inserted == 6
        assert report.rows_skipped == 0

        with connect(db_path) as conn:
            row = conn.execute(
                """SELECT session_id, note_path, vault_folder, note_type,
                          started_at, ended_at, ended_reason,
                          doc_change_count, selection_change_count,
                          insert_count, delete_count,
                          total_inserted_chars, total_deleted_chars,
                          undo_count, paste_count
                   FROM writing_sessions WHERE session_id = ?""",
                ("sess_test_1",),
            ).fetchone()

        assert row is not None
        (
            sid,
            note_path,
            vault_folder,
            note_type,
            started_at,
            ended_at,
            ended_reason,
            doc_n,
            sel_n,
            ins_n,
            del_n,
            ins_chars,
            del_chars,
            undo_n,
            paste_n,
        ) = row
        assert sid == "sess_test_1"
        assert note_path == "Notes/test.md"
        assert vault_folder == "Notes"
        assert note_type == "Observation"
        assert started_at == 1_000_000_000_100
        assert ended_at == 1_000_000_000_800
        assert ended_reason == "blur"
        assert doc_n == 5
        assert sel_n == 1
        # 'hello' (5), ' world' (6), '!' (paste, 1) — 3 inserts
        assert ins_n == 3
        # delete.backward of 'd' (1 char), undo of '!' (1 char) — 2 deletes
        assert del_n == 2
        # 5 + 6 + 1 = 12 total inserted chars
        assert ins_chars == 12
        # 1 + 1 = 2 total deleted chars
        assert del_chars == 2
        assert undo_n == 1
        assert paste_n == 1

    def test_individual_delta_rows_carry_expected_fields(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = WritingDeltasPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_BASIC, conn, settings)

        with connect(db_path) as conn:
            rows = conn.execute(
                """SELECT event_type, user_event, inserted_text, deleted_text,
                          from_a, to_a, from_b, to_b, ts, selection_ranges_json
                   FROM writing_deltas
                   WHERE session_id = ?
                   ORDER BY ts""",
                ("sess_test_1",),
            ).fetchall()

        assert len(rows) == 6
        # First event: type 'hello'
        assert rows[0][0] == "doc-change"
        assert rows[0][1] == "input.type"
        assert rows[0][2] == "hello"
        assert rows[0][3] == ""
        assert rows[0][9] is None  # selection-only column
        # Fifth event: undo
        assert rows[4][0] == "doc-change"
        assert rows[4][1] == "undo"
        assert rows[4][2] == ""
        assert rows[4][3] == "!"
        # Sixth event: selection-change
        assert rows[5][0] == "selection-change"
        assert rows[5][1] is None
        assert rows[5][2] is None  # no inserted_text on selection events
        assert rows[5][9] is not None
        import json as _json
        ranges = _json.loads(rows[5][9])
        assert ranges == [{"anchor": 0, "head": 5}]

    def test_note_switch_events_not_materialised(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = WritingDeltasPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_BASIC, conn, settings)

        with connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM writing_sessions"
            ).fetchone()[0]
            assert count == 1  # not 2 — the note-switch did not create one
            count_deltas = conn.execute(
                "SELECT COUNT(*) FROM writing_deltas"
            ).fetchone()[0]
            assert count_deltas == 6  # 5 doc-change + 1 selection-change


class TestReingestIdempotency:
    def test_reingesting_same_file_is_a_noop(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = WritingDeltasPlugin()

        with connect(db_path) as conn:
            adapter.run(FIXTURE_BASIC, conn, settings)
            first_session_count = conn.execute(
                "SELECT COUNT(*) FROM writing_sessions"
            ).fetchone()[0]
            first_delta_count = conn.execute(
                "SELECT COUNT(*) FROM writing_deltas"
            ).fetchone()[0]

        with connect(db_path) as conn:
            second_report = adapter.run(FIXTURE_BASIC, conn, settings)
            second_session_count = conn.execute(
                "SELECT COUNT(*) FROM writing_sessions"
            ).fetchone()[0]
            second_delta_count = conn.execute(
                "SELECT COUNT(*) FROM writing_deltas"
            ).fetchone()[0]

        # Counts unchanged.
        assert first_session_count == second_session_count == 1
        assert first_delta_count == second_delta_count == 6
        # Adapter still yielded the events but every delta was dedup-skipped.
        assert second_report.rows_yielded == 6
        assert second_report.rows_inserted == 0
        assert second_report.rows_skipped == 6


class TestPartialLineSafety:
    def test_trailing_partial_json_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = WritingDeltasPlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_PARTIAL, conn, settings)

        # The fixture has 2 valid doc-change lines plus a truncated trailing
        # line that fails JSON.parse. The session-start makes a session row;
        # the truncated line is logged in report.errors and skipped.
        assert report.threads_created == 1
        assert report.rows_yielded == 2
        assert report.rows_inserted == 2
        assert len(report.errors) == 1
        assert "line 4" in report.errors[0]

        with connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM writing_deltas WHERE session_id = ?",
                ("sess_test_2",),
            ).fetchone()[0]
            assert count == 2


class TestCrossFileSession:
    def test_session_spanning_two_files_merges_correctly(self, tmp_path: Path) -> None:
        """Day-1 file has session-start + 1 edit; day-2 file has 1 edit + session-end."""
        db_path, settings = _setup(tmp_path)
        adapter = WritingDeltasPlugin()

        with connect(db_path) as conn:
            adapter.run(FIXTURE_DAY1, conn, settings)
            day1 = conn.execute(
                "SELECT started_at, ended_at, doc_change_count "
                "FROM writing_sessions WHERE session_id = ?",
                ("sess_cross",),
            ).fetchone()

        assert day1 is not None
        assert day1[0] == 1_000_000_002_000  # started_at from session-start
        assert day1[1] is None  # ended_at still NULL — no session-end seen yet
        assert day1[2] == 1  # one doc-change so far

        with connect(db_path) as conn:
            adapter.run(FIXTURE_DAY2, conn, settings)
            day2 = conn.execute(
                "SELECT started_at, ended_at, ended_reason, doc_change_count "
                "FROM writing_sessions WHERE session_id = ?",
                ("sess_cross",),
            ).fetchone()

        assert day2 is not None
        # started_at preserved from day-1 (MIN-on-conflict keeps the earlier)
        assert day2[0] == 1_000_000_002_000
        # ended_at filled in from day-2's session-end
        assert day2[1] == 1_000_000_003_200
        assert day2[2] == "idle"
        # Aggregate now counts both files' doc-changes
        assert day2[3] == 2

    def test_day_2_alone_uses_ended_at_as_started_at_fallback(self, tmp_path: Path) -> None:
        """If only the session-end file is ingested, started_at = ended_at fallback."""
        db_path, settings = _setup(tmp_path)
        adapter = WritingDeltasPlugin()

        with connect(db_path) as conn:
            adapter.run(FIXTURE_DAY2, conn, settings)
            row = conn.execute(
                "SELECT started_at, ended_at FROM writing_sessions WHERE session_id = ?",
                ("sess_cross",),
            ).fetchone()

        assert row is not None
        # No real session-start seen yet — fallback to the doc-change ts (earliest event in file 2)
        assert row[0] == 1_000_000_003_100
        assert row[1] == 1_000_000_003_200

        # Re-ingest day 1 — started_at should be corrected backwards via MIN.
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DAY1, conn, settings)
            row = conn.execute(
                "SELECT started_at, ended_at FROM writing_sessions WHERE session_id = ?",
                ("sess_cross",),
            ).fetchone()

        assert row is not None
        assert row[0] == 1_000_000_002_000
        assert row[1] == 1_000_000_003_200


class TestAdapterIdentity:
    def test_iter_rows_raises_not_implemented(self, tmp_path: Path) -> None:
        adapter = WritingDeltasPlugin()
        import pytest

        with pytest.raises(NotImplementedError):
            next(adapter.iter_rows(tmp_path / "nope.ndjson"))

    def test_adapter_metadata(self) -> None:
        adapter = WritingDeltasPlugin()
        assert adapter.name == "writing_deltas"
        assert adapter.source_kind == "writing-deltas"
        assert adapter.file_kind == "ndjson"
