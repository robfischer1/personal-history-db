"""Skill Graph Phase 5 pilot driver.

Reads the seeded Programming subgraph from phdb, computes the frontier,
generates ranked action edges, and renders both surfaces (markdown digest
+ JSON Canvas). Outputs land in `Atlas/Skill Graph Pilot/`.

Run from repo root:
    python scripts/skill_graph_pilot.py

The action_provider here is the pilot's first cut — hand-authored per
discipline. Phase 6 will replace this with a learned template registry.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from phdb.skill_graph.canvas import render_canvas
from phdb.skill_graph.digest import render_digest
from phdb.skill_graph.edges import generate_actions
from phdb.skill_graph.frontier import compute_frontier
from phdb.skill_graph.models import (
    ActionEdge,
    DisciplineNode,
    FrontierReason,
    SkillGraphSnapshot,
    StructuralEdge,
)
from phdb.skill_graph.persistence import read_discipline
from phdb.skill_graph.ranker import rank_actions

DB_PATH = Path("C:/Users/robfi/Forge/personal-history-data/personal-history.db")
OUT_DIR = Path("C:/Users/robfi/Forge/Obsidian/Atlas/Skill Graph Pilot")
BOOTSTRAP_REF = "skill-graph-pilot-bootstrap-2026-05-25b"

DISCIPLINES = [
    "Programming",
    "Languages", "Web", "Tooling",
    "Python", "JavaScript", "SQL", "PHP",
    "HTML/CSS", "React",
    "Git", "Shell", "Testing",
]


# Per-discipline action templates. Hand-authored from Rob's actual context
# (Cath Lab PHP, phdb Python, vault tooling, Hephaestus PowerShell, etc.).
# Each ActionEdge: (kind, description, est_value, cost, novelty, p_action).
_ACTIONS: dict[str, list[tuple[str, str, float, float, float, float]]] = {
    "Programming": [
        ("progress", "Work through one chapter/week of Crafting Interpreters (or SICP) — write the code, not just read", 0.7, 0.5, 0.5, 0.4),
        ("quantify", "Hand-write a 200-LOC project end-to-end in any language; self-rate design/idioms/tests/debugging", 0.5, 0.3, 0.3, 0.6),
    ],
    "Python": [
        ("progress", "Pick one phdb module and refactor to type-strict (pyright strict mode); fix every report", 0.5, 0.4, 0.4, 0.5),
    ],
    "JavaScript": [
        ("progress", "Modernize one Cath Lab JS file to ES2024 + lint clean; commit", 0.5, 0.4, 0.4, 0.4),
        ("quantify", "Build a 100-LOC vanilla DOM toy parsing a phdb JSON export into a sortable table", 0.4, 0.3, 0.4, 0.5),
    ],
    "PHP": [
        ("progress", "Pull the Cath Lab framework into a local environment, get it running", 0.5, 0.6, 0.3, 0.3),
        ("quantify", "Pick 3 .plot.php framework features and re-derive them from memory before checking your old code", 0.5, 0.2, 0.4, 0.5),
    ],
    "SQL": [
        ("progress", "Convert 3 phdb queries between sqlite3 raw and a query builder; track which form catches what", 0.6, 0.4, 0.3, 0.5),
        ("quantify", "Write 5 EXPLAIN QUERY PLAN queries against phdb's heaviest tables; predict the strategy before running", 0.5, 0.2, 0.4, 0.6),
    ],
    "Shell": [
        ("progress", "Pick one phdb maintenance task; write portable Bash + PowerShell siblings of the same script", 0.4, 0.4, 0.4, 0.5),
        ("quantify", "Re-derive grep/awk/sed for one phdb log-scraping task without LLM assistance", 0.3, 0.2, 0.4, 0.5),
    ],
    "HTML/CSS": [
        ("progress", "Style one existing vault Bases dashboard from default to deliberate; commit the cssclass", 0.3, 0.3, 0.3, 0.4),
    ],
    "React": [
        ("progress", "Stand up a minimal React + Vite app that consumes one phdb MCP tool", 0.5, 0.5, 0.6, 0.3),
    ],
    "Git": [
        ("progress", "Write a short post on how phdb uses commit-trailer authorship classification (Source: Code/Cowork/Manual)", 0.4, 0.3, 0.4, 0.4),
    ],
    "Testing": [
        ("progress", "Add property-based tests (Hypothesis) to 2 pure functions in phdb", 0.6, 0.3, 0.5, 0.5),
        ("quantify", "Pick 3 existing phdb tests; write down what they actually verify vs what they appear to", 0.4, 0.2, 0.3, 0.6),
    ],
    # Composite categories — decomposition action only
    "Languages": [
        ("quantify", "Decompose 'Languages' — confirm Python/JavaScript/SQL/PHP is the right granularity (add or drop)", 0.3, 0.1, 0.3, 0.5),
    ],
    "Web": [
        ("quantify", "Decompose 'Web' — currently HTML/CSS + React; add Tailwind / state mgmt if you're picking the stack back up", 0.3, 0.1, 0.3, 0.5),
    ],
    "Tooling": [
        ("quantify", "Decompose 'Tooling' — Git/Shell/Testing today; add Docker / Linux / CI if relevant", 0.3, 0.1, 0.3, 0.5),
    ],
}


def pilot_action_provider(label: str, reason: FrontierReason) -> list[ActionEdge]:
    """Return hand-authored actions for a frontier node."""
    tmpl = _ACTIONS.get(label, [])
    out: list[ActionEdge] = []
    for kind, desc, value, cost, novelty, p_act in tmpl:
        out.append(ActionEdge(
            kind=kind,  # type: ignore[arg-type]
            target=label,
            description=desc,
            est_value=value,
            cost=cost,
            novelty=novelty,
            p_action=p_act,
        ))
    return out


def build_snapshot(conn: sqlite3.Connection) -> SkillGraphSnapshot:
    nodes: list[DisciplineNode] = []
    for label in DISCIPLINES:
        n = read_discipline(conn, label)
        nodes.append(n if n else DisciplineNode(label=label, readiness=None, last_verified=None))
    edges: list[StructuralEdge] = []
    rows = conn.execute(
        """SELECT s.label, p.name, o.label
           FROM triples t
           JOIN nodes s ON s.id = t.subject_node_id
           LEFT JOIN nodes o ON o.id = t.object_node_id
           JOIN predicates p ON p.id = t.predicate_id
           WHERE p.name IN ('prerequisiteOf', 'childOf')
             AND t.source_ref = ?""",
        (BOOTSTRAP_REF,),
    ).fetchall()
    for subj, pred, obj in rows:
        edges.append(StructuralEdge(subject=subj, predicate=pred, object=obj))
    return SkillGraphSnapshot(nodes=nodes, structural_edges=edges)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    snap = build_snapshot(conn)

    now = datetime.now(UTC)
    frontier = compute_frontier(snap, now=now)
    enriched = generate_actions(frontier, action_provider=pilot_action_provider)
    ranked = rank_actions(enriched)

    # Markdown digest
    digest = render_digest(ranked, max_actions_per_node=3)
    header = (
        "---\n"
        "\"@context\": \"https://schema.org\"\n"
        "\"@type\": \"Report\"\n"
        f"name: \"Skill Graph Pilot — Frontier Digest\"\n"
        f"created: {now.date().isoformat()}\n"
        f"updated: {now.date().isoformat()}\n"
        "author_type: \"ai-generated\"\n"
        "ai_model: \"Claude Opus 4.7 (1M context)\"\n"
        "status: Active\n"
        "execution_type: recomputed\n"
        "up: \"[[Atlas]]\"\n"
        f"tags:\n"
        "---\n\n"
    )
    (OUT_DIR / "Frontier Digest.md").write_text(header + digest, encoding="utf-8")

    # JSON Canvas — curated subgraph
    canvas = render_canvas(snap.nodes, snap.structural_edges, frontier=ranked)
    (OUT_DIR / "Skill Graph.canvas").write_text(canvas, encoding="utf-8")

    print(f"Wrote {len(ranked)} frontier entries")
    print(f"Digest: {OUT_DIR / 'Frontier Digest.md'}")
    print(f"Canvas: {OUT_DIR / 'Skill Graph.canvas'}")


if __name__ == "__main__":
    main()
