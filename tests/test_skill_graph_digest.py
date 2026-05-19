"""Tests for phdb.skill_graph.digest — markdown digest rendering."""

from __future__ import annotations

from phdb.skill_graph import ActionEdge, DisciplineNode, FrontierEntry
from phdb.skill_graph.digest import render_digest


def test_empty_frontier_renders_message() -> None:
    out = render_digest([])
    assert "Nothing on the frontier" in out
    assert "Skill-graph frontier" in out


def test_digest_contains_node_label_and_reason() -> None:
    entry = FrontierEntry(
        node=DisciplineNode(label="Spanish"),
        reason="unaddressed",
        actions=[],
    )
    out = render_digest([entry])
    assert "Spanish" in out
    assert "unaddressed" in out


def test_digest_shows_top_n_actions() -> None:
    actions = [
        ActionEdge(
            kind="progress",
            target="Spanish",
            description=f"Action {i}",
            est_value=0.5,
            cost=0.2,
            novelty=0.3,
            score=0.9 - i * 0.1,
        )
        for i in range(5)
    ]
    entry = FrontierEntry(
        node=DisciplineNode(label="Spanish"),
        reason="unaddressed",
        actions=actions,
    )
    out = render_digest([entry], max_actions_per_node=2)
    assert "Action 0" in out
    assert "Action 1" in out
    assert "Action 2" not in out


def test_delegation_recent_is_flagged() -> None:
    entry = FrontierEntry(
        node=DisciplineNode(
            label="JS",
            readiness=0.7,
            last_verified="2026-05-15",
            delegation_recent=True,
        ),
        reason="under-informed",
        actions=[],
    )
    out = render_digest([entry])
    assert "delegation recent" in out


def test_digest_count_pluralization() -> None:
    one = render_digest([FrontierEntry(node=DisciplineNode(label="A"), reason="unaddressed")])
    assert "1 discipline " in one
    assert "1 disciplines" not in one

    two = render_digest(
        [
            FrontierEntry(node=DisciplineNode(label="A"), reason="unaddressed"),
            FrontierEntry(node=DisciplineNode(label="B"), reason="unaddressed"),
        ]
    )
    assert "2 disciplines" in two


def test_digest_renders_readiness_dash_for_unaddressed() -> None:
    entry = FrontierEntry(
        node=DisciplineNode(label="Spanish"),
        reason="unaddressed",
    )
    out = render_digest([entry])
    assert "`—`" in out  # Em-dash placeholder for None readiness
    assert "`never`" in out  # Placeholder for None last_verified


def test_digest_includes_score_value_cost_novelty() -> None:
    action = ActionEdge(
        kind="progress",
        target="X",
        description="Do thing",
        est_value=0.7,
        cost=0.2,
        novelty=0.4,
        p_action=0.8,
        score=0.72,
    )
    entry = FrontierEntry(
        node=DisciplineNode(label="X"),
        reason="unaddressed",
        actions=[action],
    )
    out = render_digest([entry])
    assert "0.72" in out  # score
    assert "0.70" in out  # value
    assert "0.20" in out  # cost
    assert "0.40" in out  # novelty
    assert "0.80" in out  # p_action
