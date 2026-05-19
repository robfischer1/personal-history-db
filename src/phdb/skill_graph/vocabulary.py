"""Skill-graph vocabulary — predicate names, qualifier keys, node-kind constants.

The skill graph is layered on phdb's predicate table (migration 0012). This
module is the canonical source of names so encoders and decoders stay in sync.
Matches the schema doc at `docs/SKILL_GRAPH.md`.
"""

from __future__ import annotations

# Node kinds — use phdb's existing 'concept' kind for disciplines.
DISCIPLINE_KIND: str = "concept"

# Predicates used by the skill graph. All are camelCase per the predicates
# table convention.
PRED_PREREQUISITE_OF: str = "prerequisiteOf"  # subject must precede object
PRED_CHILD_OF: str = "childOf"  # composition / zoom hierarchy (existing predicate, reused)
PRED_HAS_READINESS: str = "hasReadiness"  # (discipline, hasReadiness, NULL) — readiness in qualifiers

# Qualifier keys on hasReadiness triples.
Q_VALUE: str = "value"  # str of float in [0.0, 1.0]
Q_LAST_VERIFIED: str = "last_verified"  # ISO 8601 timestamp
Q_DELEGATION_RECENT: str = "delegation_recent"  # "true" / "false"
Q_BASE_VALUE: str = "base_value"  # str of float — leaky integrator base (Phase 2)
Q_TIER: str = "tier"  # discipline tier — Phase 2 looks up half-life by tier
