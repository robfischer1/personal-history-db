"""Generate action edges for frontier nodes.

An *action edge* is a concrete next-action — either:

- ``quantify``: completion *measures* the target's readiness (resolves an
  under-informed node).
- ``progress``: completion *raises* the target's readiness (resolves an
  unaddressed node).

Action edges are not persisted as triples in V1 — they're generated on
demand from a pluggable `action_provider` callable that the caller supplies.
For tests and pilot fixtures, the provider can be a simple dict lookup; for
production, Phase 5 wires up a per-discipline action template registry.
"""

from __future__ import annotations

from collections.abc import Callable

from .models import ActionEdge, FrontierEntry, FrontierReason

ActionProvider = Callable[[str, FrontierReason], list[ActionEdge]]


def generate_actions(
    entries: list[FrontierEntry],
    *,
    action_provider: ActionProvider,
) -> list[FrontierEntry]:
    """Attach generated action edges to each frontier entry.

    Returns a *new* list of `FrontierEntry` with `actions` populated. The
    actions are not yet ranked — call `ranker.rank_actions` next.
    """
    enriched: list[FrontierEntry] = []
    for entry in entries:
        actions = action_provider(entry.node.label, entry.reason)
        enriched.append(
            FrontierEntry(node=entry.node, reason=entry.reason, actions=list(actions))
        )
    return enriched
