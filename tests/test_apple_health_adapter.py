"""Tests for the apple_health adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.apple_health import AppleHealthAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "apple_health" / "Health_Export.zip"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestAppleHealthIntegration:
    def test_basic_ingest_counts(self, tmp_path: Path) -> None:
        """2 Records + 1 Workout + 1 ClinicalRecord = 4 messages."""
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
        assert report.rows_inserted == 4
        assert report.rows_skipped == 0

    def test_schema_types(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            types = conn.execute(
                "SELECT schema_type, COUNT(*) FROM observations GROUP BY schema_type"
                " UNION ALL SELECT schema_type, COUNT(*) FROM exercise_actions GROUP BY schema_type"
                " UNION ALL SELECT schema_type, COUNT(*) FROM medical_records GROUP BY schema_type"
                " ORDER BY schema_type"
            ).fetchall()
        type_map = dict(types)
        assert type_map["Observation"] == 2
        assert type_map["ExerciseAction"] == 1
        assert type_map["MedicalRecord"] == 1

    def test_record_metadata_sidecar(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            meta_count = conn.execute("SELECT COUNT(*) FROM record_metadata").fetchone()[0]
        assert meta_count == 1

    def test_hr_samples_sidecar(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            hr_count = conn.execute("SELECT COUNT(*) FROM hr_samples").fetchone()[0]
        assert hr_count == 1
        with connect(db_path) as conn:
            bpm = conn.execute("SELECT bpm FROM hr_samples").fetchone()[0]
        assert bpm == 72

    def test_workout_events_sidecar(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            ev_count = conn.execute("SELECT COUNT(*) FROM workout_events").fetchone()[0]
        assert ev_count == 1
        with connect(db_path) as conn:
            ev_type = conn.execute("SELECT event_type FROM workout_events").fetchone()[0]
        assert ev_type == "pause"

    def test_workout_statistics_sidecar(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            stat_count = conn.execute("SELECT COUNT(*) FROM workout_statistics").fetchone()[0]
        assert stat_count == 1
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT stat_type, value_min, value_avg, value_max, unit FROM workout_statistics"
            ).fetchone()
        assert row[0] == "HKQuantityTypeIdentifierHeartRate"
        assert row[1] == 120.0
        assert row[2] == 145.0
        assert row[3] == 170.0
        assert row[4] == "count/min"

    def test_threads_created(self, tmp_path: Path) -> None:
        """Expect: metrics thread + workout thread + clinical thread = 3."""
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads == 3
        assert report.threads_created == 3

    def test_hk_prefix_stripped_in_subject(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            subjects = conn.execute(
                "SELECT subject FROM observations ORDER BY date_observed"
            ).fetchall()
        assert subjects[0][0].startswith("StepCount:")
        assert subjects[1][0].startswith("HeartRate:")

    def test_clinical_direction_inbound(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            d = conn.execute(
                "SELECT direction FROM medical_records"
            ).fetchone()[0]
        assert d == "inbound"

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            r2 = AppleHealthAdapter().run(FIXTURE_ZIP, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted

    def test_all_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AppleHealthAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            non_bulk = conn.execute(
                "SELECT SUM(c) FROM ("
                " SELECT COUNT(*) AS c FROM observations WHERE is_bulk != 1"
                " UNION ALL SELECT COUNT(*) FROM exercise_actions WHERE is_bulk != 1"
                " UNION ALL SELECT COUNT(*) FROM medical_records WHERE is_bulk != 1"
                ")"
            ).fetchone()[0]
        assert non_bulk == 0
