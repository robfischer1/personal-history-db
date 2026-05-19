"""Tests for phdb.skill_graph.ranker — the (value-cost+novelty)*P(action) formula."""

from __future__ import annotations

import pytest

from phdb.skill_graph import ActionEdge, DisciplineNode, FrontierEntry
from phdb.skill_graph.ranker import rank_actions, score_action


def _action(
    *,
    value: float,
    cost: float,
    novelty: float,
    p_action: float = 0.5,
    description: str = "",
) -> ActionEdge:
    return ActionEdge(
        kind="progress",
        target="X",
        description=description,
        est_value=value,
        cost=cost,
        novelty=novelty,
        p_action=p_action,
    )


def test_score_formula() -> None:
    # (0.8 - 0.2 + 0.4) * 0.5 = 0.5
    action = _action(value=0.8, cost=0.2, novelty=0.4, p_action=0.5)
    assert score_action(action) == pytest.approx(0.5)


def test_p_action_zero_zeroes_the_score() -> None:
    """A theoretically perfect edge with P(action)=0 scores 0 — actionability rules."""
    action = _action(value=1.0, cost=0.0, novelty=1.0, p_action=0.0)
    assert score_action(action) == 0.0


def test_rank_orders_descending_by_score() -> None:
    high = _action(value=0.9, cost=0.1, novelty=0.8, p_action=0.9, description="high")
    low = _action(value=0.3, cost=0.3, novelty=0.1, p_action=0.4, description="low")
    mid = _action(value=0.6, cost=0.2, novelty=0.4, p_action=0.6, description="mid")

    entries = [
        FrontierEntry(
            node=DisciplineNode(label="X"),
            reason="unaddressed",
            actions=[low, high, mid],
        )
    ]
    ranked = rank_actions(entries)
    assert [a.description for a in ranked[0].actions] == ["high", "mid", "low"]


def test_actionability_dominates_optimality() -> None:
    """The actionability principle made into code:

    a non-optimal choice Rob actually does beats an optimal one he ignores.
    """
    great_but_wont_do = _action(
        value=1.0, cost=0.0, novelty=1.0, p_action=0.05, description="great but won't do"
    )
    # Score: (1 - 0 + 1) * 0.05 = 0.1
    modest_will_do = _action(
        value=0.5, cost=0.2, novelty=0.3, p_action=0.95, description="modest will do"
    )
    # Score: (0.5 - 0.2 + 0.3) * 0.95 = 0.57

    entries = [
        FrontierEntry(
            node=DisciplineNode(label="X"),
            reason="unaddressed",
            actions=[great_but_wont_do, modest_will_do],
        )
    ]
    ranked = rank_actions(entries)
    assert ranked[0].actions[0].description == "modest will do"


def test_rank_actions_handles_empty_actions() -> None:
    entries = [FrontierEntry(node=DisciplineNode(label="X"), reason="unaddressed", actions=[])]
    ranked = rank_actions(entries)
    assert ranked[0].actions == []


def test_rank_actions_handles_no_entries() -> None:
    assert rank_actions([]) == []
