"""Tests for phdb.skill_graph.frontier — pure-Python, no DB."""

from __future__ import annotations

from datetime import datetime, timedelta

from phdb.skill_graph import DisciplineNode, SkillGraphSnapshot, StructuralEdge
from phdb.skill_graph.frontier import compute_frontier

# Reference "now" used by every test for deterministic staleness checks.
NOW = datetime(2026, 5, 19, 12, 0, 0)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def test_unaddressed_node_with_no_prereqs_is_on_frontier() -> None:
    snapshot = SkillGraphSnapshot(
        nodes=[DisciplineNode(label="Spanish")],
        structural_edges=[],
    )
    frontier = compute_frontier(snapshot, now=NOW)
    assert len(frontier) == 1
    assert frontier[0].node.label == "Spanish"
    assert frontier[0].reason == "unaddressed"


def test_addressed_fresh_node_is_not_on_frontier() -> None:
    snapshot = SkillGraphSnapshot(
        nodes=[DisciplineNode(label="Python", readiness=0.7, last_verified=iso(NOW))],
        structural_edges=[],
    )
    assert compute_frontier(snapshot, now=NOW) == []


def test_addressed_stale_node_is_under_informed() -> None:
    snapshot = SkillGraphSnapshot(
        nodes=[
            DisciplineNode(
                label="Python",
                readiness=0.7,
                last_verified=iso(NOW - timedelta(days=120)),
            )
        ],
        structural_edges=[],
    )
    frontier = compute_frontier(snapshot, now=NOW)
    assert len(frontier) == 1
    assert frontier[0].reason == "under-informed"


def test_missing_last_verified_is_under_informed() -> None:
    snapshot = SkillGraphSnapshot(
        nodes=[DisciplineNode(label="Python", readiness=0.7, last_verified=None)],
        structural_edges=[],
    )
    frontier = compute_frontier(snapshot, now=NOW)
    assert frontier[0].reason == "under-informed"


def test_malformed_last_verified_is_under_informed() -> None:
    snapshot = SkillGraphSnapshot(
        nodes=[DisciplineNode(label="Python", readiness=0.7, last_verified="not-a-timestamp")],
        structural_edges=[],
    )
    frontier = compute_frontier(snapshot, now=NOW)
    assert frontier[0].reason == "under-informed"


def test_unaddressed_node_with_unsatisfied_prereq_is_excluded() -> None:
    snapshot = SkillGraphSnapshot(
        nodes=[
            DisciplineNode(label="JS"),  # unaddressed
            DisciplineNode(label="React"),  # unaddressed, needs JS
        ],
        structural_edges=[StructuralEdge("JS", "prerequisiteOf", "React")],
    )
    frontier = compute_frontier(snapshot, now=NOW)
    # JS is on the frontier (no prereqs). React is NOT (its prereq JS isn't on the front).
    assert {e.node.label for e in frontier} == {"JS"}


def test_unaddressed_node_with_satisfied_prereq_is_on_frontier() -> None:
    snapshot = SkillGraphSnapshot(
        nodes=[
            DisciplineNode(label="JS", readiness=0.7, last_verified=iso(NOW)),
            DisciplineNode(label="React"),  # unaddressed
        ],
        structural_edges=[StructuralEdge("JS", "prerequisiteOf", "React")],
    )
    frontier = compute_frontier(snapshot, now=NOW)
    assert {e.node.label for e in frontier} == {"React"}


def test_permissive_any_prereq_unlocks_node() -> None:
    """A node with multiple prereqs unlocks when AT LEAST ONE is on the front."""
    snapshot = SkillGraphSnapshot(
        nodes=[
            DisciplineNode(label="JS", readiness=0.7, last_verified=iso(NOW)),
            DisciplineNode(label="Python"),  # not on the front
            DisciplineNode(label="FullStack"),  # needs both JS and Python
        ],
        structural_edges=[
            StructuralEdge("JS", "prerequisiteOf", "FullStack"),
            StructuralEdge("Python", "prerequisiteOf", "FullStack"),
        ],
    )
    frontier = compute_frontier(snapshot, now=NOW)
    labels = {e.node.label for e in frontier}
    assert "FullStack" in labels  # JS satisfies → unlocked
    assert "Python" in labels  # no prereqs → unlocked too


def test_threshold_override() -> None:
    """A fresh node below the threshold surfaces as below-threshold;
    lowering the threshold clears it and unlocks dependents instead."""
    snapshot = SkillGraphSnapshot(
        nodes=[
            DisciplineNode(label="JS", readiness=0.25, last_verified=iso(NOW)),  # low but fresh
            DisciplineNode(label="React"),
        ],
        structural_edges=[StructuralEdge("JS", "prerequisiteOf", "React")],
    )

    # Default threshold 0.3 — JS is fresh but below the front -> below-threshold
    # reason; React's prereq unmet (JS not on the front), so React is excluded.
    default = compute_frontier(snapshot, now=NOW)
    assert {e.node.label for e in default} == {"JS"}
    assert default[0].reason == "below-threshold"

    # Lower threshold to 0.2 — JS clears (no longer below-threshold), React unlocks.
    lowered = compute_frontier(snapshot, now=NOW, readiness_threshold=0.2)
    assert {e.node.label for e in lowered} == {"React"}


def test_below_threshold_exempt_from_prereq_gate() -> None:
    """A below-threshold node surfaces even when its own prereqs aren't on the front."""
    snapshot = SkillGraphSnapshot(
        nodes=[
            DisciplineNode(label="Programming", readiness=0.2, last_verified=iso(NOW)),  # below threshold
            DisciplineNode(label="Python", readiness=0.15, last_verified=iso(NOW)),  # below threshold; needs Programming
        ],
        structural_edges=[StructuralEdge("Programming", "prerequisiteOf", "Python")],
    )
    frontier = compute_frontier(snapshot, now=NOW)
    # Both surface as below-threshold despite Python's prereq (Programming) not being on the front.
    assert {e.node.label for e in frontier} == {"Programming", "Python"}
    assert all(e.reason == "below-threshold" for e in frontier)


def test_tz_aware_now_compares_against_tz_aware_last_verified() -> None:
    """Mixed naive/aware datetimes shouldn't TypeError — they get normalized."""
    from datetime import UTC

    aware_now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    snapshot = SkillGraphSnapshot(
        nodes=[
            DisciplineNode(
                label="Python",
                readiness=0.7,
                last_verified="2026-05-19T11:00:00+00:00",  # tz-aware ISO
            )
        ],
        structural_edges=[],
    )
    # Must not TypeError. Python is fresh (1 hour ago) + above threshold → not frontier.
    assert compute_frontier(snapshot, now=aware_now) == []


def test_staleness_threshold_override() -> None:
    snapshot = SkillGraphSnapshot(
        nodes=[
            DisciplineNode(
                label="Python",
                readiness=0.7,
                last_verified=iso(NOW - timedelta(days=30)),
            )
        ],
        structural_edges=[],
    )
    # Default 60-day staleness — 30 days = fresh.
    assert compute_frontier(snapshot, now=NOW) == []

    # Tighter 14-day staleness — now under-informed.
    tighter = compute_frontier(snapshot, now=NOW, staleness_days=14)
    assert len(tighter) == 1
    assert tighter[0].reason == "under-informed"
