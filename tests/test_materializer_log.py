"""Tests for phdb.dissolutions.materializer_log (Phase 8 of Dissolution Tracking)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from phdb import dissolutions as dis
from phdb.db import connect
from phdb.dissolutions.materializer_log import MaterializationLogger


def test_logger_records_via_helper(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHDB_DB_PATH", str(migrated_db))
    with MaterializationLogger(materializer="test_materializer") as ml:
        new_id = ml.log_stub(
            file_path="References/foo.md",
            source_table="articles",
            source_row_id=42,
        )
        assert new_id is not None
        assert new_id > 0

    # Round-trip via lookup
    with connect(migrated_db, readonly=True) as conn:
        result = dis.lookup_vault_path(conn, "References/foo.md")
        assert len(result["materializations"]) == 1
        m = result["materializations"][0]
        assert m["source_table"] == "articles"
        assert m["source_row_id"] == 42
        assert m["materializer"] == "test_materializer"
        assert m["materialization_kind"] == "stub"


def test_logger_aggregate_kind(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHDB_DB_PATH", str(migrated_db))
    with MaterializationLogger(materializer="gen_todo_md") as ml:
        new_id = ml.log_aggregate(
            file_path="System/TODO.md",
            source_table="tasks",
        )
        assert new_id is not None

    with connect(migrated_db, readonly=True) as conn:
        row = conn.execute(
            "SELECT materialization_kind FROM materialization_events WHERE id = ?",
            (new_id,),
        ).fetchone()
        assert row[0] == "aggregate"


def test_logger_db_unavailable_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Point at a non-existent DB. Pass it as override to bypass the fallback path.
    fake = tmp_path / "missing.db"
    monkeypatch.delenv("PHDB_DB_PATH", raising=False)
    # Block the fallback path by stubbing _resolve_db_path indirectly:
    # explicitly pass a missing path via the constructor.
    ml = MaterializationLogger(materializer="test", db_path=str(fake))
    # The logger's _ensure_conn will hit OperationalError trying to open the
    # non-existent file and disable itself.
    result = ml.log_stub(
        file_path="x.md",
        source_table="articles",
    )
    assert result is None
    ml.close()


def test_logger_idempotent_under_repeated_materialization(
    migrated_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated materialization of the same path == multiple events (intentional)."""
    monkeypatch.setenv("PHDB_DB_PATH", str(migrated_db))
    with MaterializationLogger(materializer="test") as ml:
        id1 = ml.log_stub(
            file_path="References/foo.md",
            source_table="articles",
            source_row_id=42,
        )
        id2 = ml.log_stub(
            file_path="References/foo.md",
            source_table="articles",
            source_row_id=42,
        )
    # Distinct rows by design — re-materialization is a real event.
    assert id1 != id2
    with connect(migrated_db, readonly=True) as conn:
        result = dis.lookup_vault_path(conn, "References/foo.md")
        assert len(result["materializations"]) == 2
