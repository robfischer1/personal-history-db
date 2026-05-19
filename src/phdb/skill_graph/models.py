"""Dataclasses for the skill graph's in-memory representation.

Phase 3 (frontier, edges, ranker) and Phase 4 (digest, canvas) are pure
functions over these models. Phase 5 wires phdb reads into snapshot
construction; the algorithm modules never touch a DB connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActionKind = Literal["quantify", "progress"]
FrontierReason = Literal["unaddressed", "under-informed"]


@dataclass
class DisciplineNode:
    """A learnable discipline with optional readiness state.

    `readiness=None` means *unaddressed* — Rob has no signal here yet. A
    `readiness` value with a stale `last_verified` is *under-informed*. The
    frontier algorithm uses these states to decide what to surface.

    `delegation_recent` indicates Rob recently chose AI for this discipline;
    it's a point-in-time assessment that itself perishes (per the readiness
    leaky integrator).
    """

    label: str
    readiness: float | None = None
    last_verified: str | None = None  # ISO 8601
    delegation_recent: bool = False


@dataclass
class StructuralEdge:
    """A structural relation between disciplines.

    `predicate` is one of `prerequisiteOf` (skeleton) or `childOf` (zoom /
    composition hierarchy). Other predicates are ignored by the frontier
    algorithm.
    """

    subject: str
    predicate: str
    object: str


@dataclass
class ActionEdge:
    """A concrete next-action — the exoskeleton's primary output.

    Two kinds:
    - `quantify`: completion *measures* the target's readiness (resolves an
      under-informed node).
    - `progress`: completion *raises* the target's readiness (resolves an
      unaddressed node).

    `p_action` is the probability Rob actually acts. Phase 6 learns this from
    observed action / inaction; V1 ships with a fixed prior (0.5).
    `score` is set by the ranker.
    """

    kind: ActionKind
    target: str
    description: str
    est_value: float
    cost: float
    novelty: float
    p_action: float = 0.5
    score: float = 0.0


@dataclass
class FrontierEntry:
    """A frontier node plus its (eventually-ranked) action edges."""

    node: DisciplineNode
    reason: FrontierReason
    actions: list[ActionEdge] = field(default_factory=list)


@dataclass
class SkillGraphSnapshot:
    """An in-memory snapshot of the skill graph.

    Phase 3 algorithm functions take this as input. Phase 5 builds it from
    phdb reads.
    """

    nodes: list[DisciplineNode]
    structural_edges: list[StructuralEdge]
