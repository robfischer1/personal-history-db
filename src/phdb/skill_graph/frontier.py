"""Compute the skill-graph frontier from a snapshot.

The frontier is the set of 1-hop nodes (one prerequisite step beyond Rob's
current readiness front) that are either:
- *unaddressed* — no readiness value yet, or
- *under-informed* — readiness present but `last_verified` is stale.

A node is "on the front" when its readiness is at or above a threshold and
its `last_verified` is fresh. A frontier candidate is reachable when it has
no prerequisites OR at least one of its prerequisites is on the front
(permissive any-prereq semantics — see `_prereqs_satisfied`).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import DisciplineNode, FrontierEntry, FrontierReason, SkillGraphSnapshot
from .vocabulary import PRED_PREREQUISITE_OF

# Tunable defaults — overridable per call. Tuned empirically per the plan.
DEFAULT_READINESS_FRONT_THRESHOLD: float = 0.3  # Below this, a node isn't on the front.
DEFAULT_STALENESS_DAYS: int = 60  # Older than this = under-informed.


def compute_frontier(
    snapshot: SkillGraphSnapshot,
    *,
    readiness_threshold: float = DEFAULT_READINESS_FRONT_THRESHOLD,
    staleness_days: int = DEFAULT_STALENESS_DAYS,
    now: datetime | None = None,
) -> list[FrontierEntry]:
    """Return the frontier entries (no action edges attached yet).

    A node X is on the frontier iff:
    1. X is unaddressed (readiness is None) OR under-informed (stale), AND
    2. X has no prerequisites OR at least one prerequisite is on the front
       (readiness >= threshold AND fresh).

    Args:
        snapshot: The current skill-graph state.
        readiness_threshold: Minimum readiness for a node to count as on the front.
        staleness_days: Older `last_verified` = under-informed.
        now: Override for testing; defaults to `datetime.now()`.

    Returns:
        Frontier entries. Use `edges.generate_actions` to attach action edges,
        then `ranker.rank_actions` to score them.
    """
    if now is None:
        now = datetime.now()  # noqa: DTZ005 — naive datetime by design; ISO timestamps may be naive.

    nodes_by_label = {n.label: n for n in snapshot.nodes}

    # Map: node label → list of prerequisite labels.
    prereqs_by_node: dict[str, list[str]] = {}
    for edge in snapshot.structural_edges:
        if edge.predicate == PRED_PREREQUISITE_OF:
            # subject prerequisiteOf object → to learn `object`, you need `subject` first.
            prereqs_by_node.setdefault(edge.object, []).append(edge.subject)

    staleness_cutoff = now - timedelta(days=staleness_days)
    entries: list[FrontierEntry] = []

    for node in snapshot.nodes:
        reason = _frontier_reason(node, staleness_cutoff)
        if reason is None:
            continue
        prereqs = prereqs_by_node.get(node.label, [])
        if prereqs and not _prereqs_satisfied(prereqs, nodes_by_label, readiness_threshold):
            continue
        entries.append(FrontierEntry(node=node, reason=reason))

    return entries


def _frontier_reason(
    node: DisciplineNode,
    staleness_cutoff: datetime,
) -> FrontierReason | None:
    """Return why `node` is on the frontier, or None if it isn't."""
    if node.readiness is None:
        return "unaddressed"
    if node.last_verified is None:
        return "under-informed"
    try:
        verified_at = datetime.fromisoformat(node.last_verified)
    except ValueError:
        # Malformed timestamp — treat as under-informed so it gets re-probed.
        return "under-informed"
    if verified_at < staleness_cutoff:
        return "under-informed"
    return None


def _prereqs_satisfied(
    prereqs: list[str],
    nodes_by_label: dict[str, DisciplineNode],
    readiness_threshold: float,
) -> bool:
    """Permissive: at least one prerequisite on the front unlocks the node.

    Strict "all prereqs on the front" would dead-end disciplines on one
    unaddressed prereq; the permissive version matches how learning actually
    proceeds (you start React with some JS knowledge, not all of it).
    """
    for prereq_label in prereqs:
        prereq = nodes_by_label.get(prereq_label)
        if prereq is None:
            continue
        if prereq.readiness is not None and prereq.readiness >= readiness_threshold:
            return True
    return False
