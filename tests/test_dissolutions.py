"""Tests for phdb.dissolutions (migration 0041, Dissolution Tracking plan)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phdb import dissolutions as dis
from phdb.db import connect


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dis_db(migrated_db: Path) -> Path:
    """A migrated DB seeded with a few file_revisions rows for link testing."""
    with connect(migrated_db) as conn:
        # Insert 3 delete rows matching Entities/Books/
        for i in range(3):
            conn.execute(
                "INSERT INTO file_revisions"
                " (repo, commit_sha, file_path, git_blob_sha, parent_blob_sha,"
                "  change_type, authorship, captured_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("vault", "a" * 40, f"Entities/Books/Book {i}.md",
                 "0" * 40, "b" * 40, "delete", "ai",
                 "2026-05-23T00:00:00Z"),
            )
        # Insert one unrelated delete
        conn.execute(
            "INSERT INTO file_revisions"
            " (repo, commit_sha, file_path, git_blob_sha, parent_blob_sha,"
            "  change_type, authorship, captured_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("vault", "a" * 40, "Other/path.md",
             "0" * 40, "c" * 40, "delete", "ai",
             "2026-05-23T00:00:00Z"),
        )
        conn.commit()
    return migrated_db


# ---------------------------------------------------------------------------
# declare() — Q3 (nullable migration), Q10 (idempotency), Q11 (validation)
# ---------------------------------------------------------------------------


def test_declare_with_migration_succeeds(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        # 0041_dissolutions is the migration we're testing; use it as the FK
        new_id = dis.declare(
            conn,
            plan_slug="test-wave-1",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
            declared_by="code",
        )
        assert new_id > 0
        row = dis.get(conn, new_id)
        assert row is not None
        assert row["plan_slug"] == "test-wave-1"
        assert row["target_schemas"] == ["Book"]
        assert row["target_tables"] == ["books"]


def test_declare_without_migration_requires_rationale(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        with pytest.raises(ValueError, match="rationale required"):
            dis.declare(
                conn,
                plan_slug="test-pilot",
                target_schemas=["Article"],
                target_tables=["articles"],
                migration_id=None,
                rationale=None,
                dissolved_at="2026-05-19",
            )


def test_declare_without_migration_requires_target_tables(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        with pytest.raises(ValueError, match="target_tables must be non-empty"):
            dis.declare(
                conn,
                plan_slug="test-pilot",
                target_schemas=["Article"],
                target_tables=[],
                migration_id=None,
                rationale="testing",
                dissolved_at="2026-05-19",
            )


def test_declare_without_migration_with_rationale_succeeds(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        new_id = dis.declare(
            conn,
            plan_slug="test-pilot",
            target_schemas=["Article"],
            target_tables=["articles"],
            migration_id=None,
            rationale="pilot — no migration",
            dissolved_at="2026-05-19",
        )
        assert new_id > 0
        row = dis.get(conn, new_id)
        assert row["migration_id"] is None
        assert row["rationale"] == "pilot — no migration"


def test_declare_with_unknown_migration_fails(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        with pytest.raises(ValueError, match="not found in schema_migrations"):
            dis.declare(
                conn,
                plan_slug="test-wave",
                target_schemas=["Book"],
                target_tables=["books"],
                migration_id="9999_nonexistent",
                dissolved_at="2026-05-23",
            )


def test_declare_is_idempotent_on_plan_pk_migration(dis_db: Path) -> None:
    """Q10 — re-declaring the same (plan_pk, migration_id) returns existing id."""
    with connect(dis_db) as conn:
        # Seed a plans row so plan_pk resolves
        conn.execute(
            "INSERT INTO plans (name, identifier, status)"
            " VALUES (?, ?, ?)",
            ("Test Plan", "test-idem-plan", "complete"),
        )
        conn.commit()

        id1 = dis.declare(
            conn,
            plan_slug="test-idem-plan",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
        )
        id2 = dis.declare(
            conn,
            plan_slug="test-idem-plan",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
        )
        assert id1 == id2


# ---------------------------------------------------------------------------
# link_file_revisions / reclassify_wave
# ---------------------------------------------------------------------------


def test_link_file_revisions_is_idempotent(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        new_id = dis.declare(
            conn,
            plan_slug="test-link",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
        )
        rev_ids = [r[0] for r in conn.execute(
            "SELECT id FROM file_revisions WHERE file_path LIKE 'Entities/Books/%'"
        ).fetchall()]
        assert len(rev_ids) == 3

        first = dis.link_file_revisions(conn, new_id, rev_ids)
        assert first == 3
        # Second run inserts nothing.
        second = dis.link_file_revisions(conn, new_id, rev_ids)
        assert second == 0


def test_reclassify_wave_matches_patterns(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        new_id = dis.declare(
            conn,
            plan_slug="test-pattern",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
        )
        result = dis.reclassify_wave(
            conn,
            new_id,
            file_path_patterns=["Entities/Books/%"],
        )
        assert result["matched"] == 3
        assert result["inserted"] == 3
        # Verify the unrelated delete was NOT linked
        rows = conn.execute(
            "SELECT file_revision_pk FROM file_revision_dissolutions"
            " WHERE dissolution_pk = ?", (new_id,),
        ).fetchall()
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# lookup_vault_path — roundtrip
# ---------------------------------------------------------------------------


def test_lookup_vault_path_roundtrip(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        new_id = dis.declare(
            conn,
            plan_slug="test-lookup",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
        )
        dis.reclassify_wave(
            conn, new_id,
            file_path_patterns=["Entities/Books/%"],
        )
        # Also record a materialization
        dis.record_materialization(
            conn,
            file_path="Entities/Books/Book 0.md",
            source_table="books",
            source_row_id=99,
            materializer="test_materializer",
            materialization_kind="stub",
            materialized_at="2026-05-24T10:00:00.000Z",
        )

        lookup = dis.lookup_vault_path(conn, "Entities/Books/Book 0.md")
        assert len(lookup["dissolutions"]) == 1
        assert len(lookup["materializations"]) == 1
        assert len(lookup["lifecycle"]) == 2
        # Chronologically ordered
        assert lookup["lifecycle"][0]["event_type"] == "dissolution"
        assert lookup["lifecycle"][1]["event_type"] == "materialization"


# ---------------------------------------------------------------------------
# validate_all / audit
# ---------------------------------------------------------------------------


def test_validate_all_passes_on_empty_registry(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        report = dis.validate_all(conn)
        assert report["pass"] is True
        assert report["error_count"] == 0


def test_validate_all_after_declare_passes(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        dis.declare(
            conn,
            plan_slug="test-valid",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
        )
        report = dis.validate_all(conn)
        assert report["pass"] is True


# ---------------------------------------------------------------------------
# Multi-repo isolation (Q14)
# ---------------------------------------------------------------------------


def test_multi_repo_isolation(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        dis.declare(
            conn,
            plan_slug="vault-wave",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
            repo="vault",
        )
        # The same plan_slug + migration_id in a different repo — should NOT
        # collide because the dedup index is (plan_pk, migration_id), but
        # plan_pk is NULL for a slug that doesn't exist in plans.
        # Note: SQLite NULL is distinct, so two (NULL, '0041_dissolutions')
        # rows are allowed — that's the Q10 NULL-tolerant behavior.
        dis.declare(
            conn,
            plan_slug="vault-wave",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
            repo="phdb",
        )
        vault_waves = dis.list_waves(conn, repo="vault")
        phdb_waves = dis.list_waves(conn, repo="phdb")
        assert len(vault_waves) == 1
        assert len(phdb_waves) == 1
        assert vault_waves[0]["repo"] == "vault"
        assert phdb_waves[0]["repo"] == "phdb"


# ---------------------------------------------------------------------------
# record_materialization — Phase 8 / Q13
# ---------------------------------------------------------------------------


def test_record_materialization_basic(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        new_id = dis.record_materialization(
            conn,
            file_path="References/foo.md",
            source_table="articles",
            source_row_id=42,
            materializer="articles_materialize",
            materialization_kind="stub",
        )
        assert new_id > 0
        # Verify shape via the view
        row = conn.execute(
            "SELECT file_path, source_table, materialization_kind"
            " FROM materialization_events WHERE id = ?",
            (new_id,),
        ).fetchone()
        assert row[0] == "References/foo.md"
        assert row[1] == "articles"
        assert row[2] == "stub"


def test_record_materialization_aggregate(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        new_id = dis.record_materialization(
            conn,
            file_path="System/TODO.md",
            source_table="tasks",
            materializer="gen_todo_md",
            materialization_kind="aggregate",
        )
        assert new_id > 0


# ---------------------------------------------------------------------------
# list_waves
# ---------------------------------------------------------------------------


def test_list_waves_counts_files(dis_db: Path) -> None:
    with connect(dis_db) as conn:
        new_id = dis.declare(
            conn,
            plan_slug="test-count",
            target_schemas=["Book"],
            target_tables=["books"],
            migration_id="0041_dissolutions",
            dissolved_at="2026-05-23",
        )
        dis.reclassify_wave(
            conn, new_id,
            file_path_patterns=["Entities/Books/%"],
        )
        waves = dis.list_waves(conn)
        assert len(waves) == 1
        assert waves[0]["linked_files"] == 3
