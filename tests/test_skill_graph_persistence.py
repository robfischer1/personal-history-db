"""Tests for phdb.skill_graph.persistence — uses the migrated_db fixture."""

from __future__ import annotations

from pathlib import Path

from phdb.db import connect
from phdb.skill_graph.persistence import (
    ensure_skill_graph_predicates,
    read_discipline,
    write_readiness,
)


def test_ensure_predicates_idempotent(migrated_db: Path) -> None:
    """Calling ensure_skill_graph_predicates twice must not duplicate rows."""
    with connect(migrated_db) as conn:
        ensure_skill_graph_predicates(conn)
        ensure_skill_graph_predicates(conn)

        rows = conn.execute(
            "SELECT COUNT(*) FROM predicates WHERE name IN ('prerequisiteOf', 'hasReadiness')"
        ).fetchone()
        assert rows[0] == 2


def test_read_nonexistent_returns_none(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        assert read_discipline(conn, "DoesNotExist") is None


def test_write_then_read_round_trip(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        write_readiness(
            conn,
            "Python",
            value=0.65,
            last_verified="2026-05-19T12:00:00Z",
            delegation_recent=False,
            base_value=0.65,
            tier="active",
        )
        node = read_discipline(conn, "Python")
        assert node is not None
        assert node.label == "Python"
        assert node.readiness is not None
        assert abs(node.readiness - 0.65) < 1e-3
        assert node.last_verified == "2026-05-19T12:00:00Z"
        assert node.delegation_recent is False


def test_write_replaces_qualifiers_on_update(migrated_db: Path) -> None:
    """Second write to the same discipline replaces (not appends) qualifiers."""
    with connect(migrated_db) as conn:
        write_readiness(
            conn,
            "Python",
            value=0.5,
            last_verified="2026-05-01T00:00:00Z",
        )
        write_readiness(
            conn,
            "Python",
            value=0.7,
            last_verified="2026-05-19T00:00:00Z",
            delegation_recent=True,
        )

        node = read_discipline(conn, "Python")
        assert node is not None
        assert abs(node.readiness - 0.7) < 1e-3  # type: ignore[operator]
        assert node.last_verified == "2026-05-19T00:00:00Z"
        assert node.delegation_recent is True

        # Verify the qualifiers table has the post-replacement state, not appended.
        row = conn.execute(
            "SELECT COUNT(*) FROM qualifiers q"
            " JOIN triples t ON t.id = q.triple_id"
            " JOIN nodes n ON n.id = t.subject_node_id"
            " JOIN predicates p ON p.id = t.predicate_id"
            " WHERE n.normalized_label = 'python' AND p.name = 'hasReadiness'"
            "   AND q.key = 'value'"
        ).fetchone()
        # Exactly one 'value' qualifier (no append-duplication).
        assert row[0] == 1


def test_write_with_none_value_persists_other_qualifiers(migrated_db: Path) -> None:
    """A discipline can be in 'unaddressed' state (no readiness) but still tracked."""
    with connect(migrated_db) as conn:
        write_readiness(
            conn,
            "Spanish",
            value=None,
            last_verified="2026-05-19T00:00:00Z",
        )
        node = read_discipline(conn, "Spanish")
        assert node is not None
        assert node.readiness is None
        assert node.last_verified == "2026-05-19T00:00:00Z"


def test_write_creates_discipline_node_with_concept_kind(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        write_readiness(
            conn,
            "NovelDiscipline",
            value=0.3,
            last_verified="2026-05-19T00:00:00Z",
        )
        row = conn.execute(
            "SELECT kind FROM nodes WHERE normalized_label = ?",
            ("noveldiscipline",),
        ).fetchone()
        assert row is not None
        assert row[0] == "concept"
