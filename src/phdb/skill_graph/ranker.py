"""Rank action edges by ``(value − cost + novelty) × P(action)``.

Per the Skill Graph plan D10 and Rob's Connections-ADHD-Game rubric
(see `user_typed_graph_cognition` memory): value of the target minus
traversal cost plus novelty, multiplied by the probability Rob actually acts.

The P(action) multiplier is the actionability lever — a great edge Rob
won't do scores below a decent edge he will. P(action) starts as a fixed
prior (0.5) in V1; Phase 6 learns it from observed action / inaction.
"""

from __future__ import annotations

from dataclasses import replace

from .models import ActionEdge, FrontierEntry


def score_action(action: ActionEdge) -> float:
    """Compute ``(est_value − cost + novelty) × p_action``."""
    return (action.est_value - action.cost + action.novelty) * action.p_action


def rank_actions(entries: list[FrontierEntry]) -> list[FrontierEntry]:
    """Score every action edge and sort each entry's actions descending by score."""
    ranked: list[FrontierEntry] = []
    for entry in entries:
        scored = [replace(action, score=score_action(action)) for action in entry.actions]
        scored.sort(key=lambda a: a.score, reverse=True)
        ranked.append(FrontierEntry(node=entry.node, reason=entry.reason, actions=scored))
    return ranked
