"""Skill graph — the exoskeleton's first job.

The skill graph is a layered application over phdb's predicate table
(migration 0012). It models Rob's learnable disciplines, computes the
1-hop frontier of unaddressed / under-informed nodes, enumerates ranked
next-actions, and surfaces them via two surfaces — a text digest and an
auto-generated Obsidian Canvas of the curated subgraph.

See ``Outputs/Plans/Skill Graph.md`` in the vault for the plan, and
``docs/SKILL_GRAPH.md`` (this repo) for the schema.

- **Phase 1** (schema) — :mod:`vocabulary`
- **Phase 2** (readiness engine) — :mod:`readiness`, :mod:`practice_events`,
  :mod:`persistence`, :mod:`engine`
- **Phase 3** (frontier + edges + ranker) — :mod:`models`, :mod:`frontier`,
  :mod:`edges`, :mod:`ranker`
- **Phase 4** (surfacing) — :mod:`digest`, :mod:`canvas`
- **Phase 5** (pilot) — wires these against a populated Programming subgraph.
"""

from __future__ import annotations

from .engine import ReadinessUpdate, update_discipline_readiness
from .models import (
    ActionEdge,
    ActionKind,
    DisciplineNode,
    FrontierEntry,
    FrontierReason,
    SkillGraphSnapshot,
    StructuralEdge,
)
from .persistence import (
    ensure_skill_graph_predicates,
    read_discipline,
    write_readiness,
)
from .practice_events import (
    CommitInfo,
    DisciplineMapper,
    PracticeEvent,
    default_discipline_mapper,
    extract_practice_events,
    filter_and_map,
)
from .readiness import (
    SkillGraphConfig,
    atrophy_alarm,
    compute_readiness,
    days_since,
    predict_decay,
)
from .vocabulary import (
    DISCIPLINE_KIND,
    PRED_CHILD_OF,
    PRED_HAS_READINESS,
    PRED_PREREQUISITE_OF,
    Q_BASE_VALUE,
    Q_DELEGATION_RECENT,
    Q_LAST_VERIFIED,
    Q_TIER,
    Q_VALUE,
)

__all__ = [
    # Models
    "ActionEdge",
    "ActionKind",
    "DisciplineNode",
    "FrontierEntry",
    "FrontierReason",
    "SkillGraphSnapshot",
    "StructuralEdge",
    # Vocabulary
    "DISCIPLINE_KIND",
    "PRED_CHILD_OF",
    "PRED_HAS_READINESS",
    "PRED_PREREQUISITE_OF",
    "Q_BASE_VALUE",
    "Q_DELEGATION_RECENT",
    "Q_LAST_VERIFIED",
    "Q_TIER",
    "Q_VALUE",
    # Readiness engine
    "SkillGraphConfig",
    "atrophy_alarm",
    "compute_readiness",
    "days_since",
    "predict_decay",
    # Practice events
    "CommitInfo",
    "DisciplineMapper",
    "PracticeEvent",
    "default_discipline_mapper",
    "extract_practice_events",
    "filter_and_map",
    # Persistence
    "ensure_skill_graph_predicates",
    "read_discipline",
    "write_readiness",
    # Engine orchestrator
    "ReadinessUpdate",
    "update_discipline_readiness",
]
