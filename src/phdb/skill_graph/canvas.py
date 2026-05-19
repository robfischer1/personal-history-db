"""Render a curated skill-graph subgraph as JSON Canvas (.canvas).

This is the *minimal* renderer for the Phase 5 pilot — given a selection of
nodes and structural edges, emit a valid `.canvas` JSON. Layout is a simple
hierarchical grid by `childOf` depth.

Format spec: <https://jsoncanvas.org/> and the Kepano `json-canvas` skill at
`github.com/kepano/obsidian-skills`.

**Curation is the caller's responsibility.** This renderer is content-neutral:
it just translates a chosen subgraph to JSON Canvas. Phase 5 decides which
nodes to include (the curated subset that gives the visual its meaning, per
`user_no_graph_view` — auto-generated *uncurated* graph rendering is out).

If the Canvas wins the Phase 5 surface pick, the generic `.canvas` writer is
promoted to a vault-mcp `write_canvas` tool (pilot-gated per the plan).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import DisciplineNode, FrontierEntry, StructuralEdge
from .vocabulary import PRED_CHILD_OF

_NODE_WIDTH = 220
_NODE_HEIGHT = 60
_COL_SPACING = 60
_ROW_SPACING = 40

# Obsidian Canvas preset colors (1-6). Used to highlight frontier nodes by reason.
_COLOR_BY_REASON: dict[str, str] = {
    "unaddressed": "5",  # purple — needs progress
    "under-informed": "3",  # yellow — needs re-probe
}


def render_canvas(
    nodes: list[DisciplineNode],
    edges: list[StructuralEdge],
    *,
    frontier: list[FrontierEntry] | None = None,
) -> str:
    """Return the JSON Canvas string.

    Args:
        nodes: Discipline nodes in the curated subgraph.
        edges: Structural edges between them. Edges referencing nodes outside
            ``nodes`` are dropped silently.
        frontier: Optional frontier entries. If provided, frontier nodes are
            color-coded by `reason` (curation carries signal per
            `user_no_graph_view`).

    Returns:
        JSON string suitable for writing to a ``.canvas`` file.
    """
    node_labels = {n.label for n in nodes}
    valid_edges = [e for e in edges if e.subject in node_labels and e.object in node_labels]

    frontier_reasons: dict[str, str] = {}
    if frontier is not None:
        for entry in frontier:
            frontier_reasons[entry.node.label] = entry.reason

    depths = _compute_depths(nodes, valid_edges)
    by_depth: dict[int, list[str]] = {}
    for label, depth in depths.items():
        by_depth.setdefault(depth, []).append(label)

    canvas_nodes: list[dict[str, Any]] = []
    for depth, labels_at_depth in sorted(by_depth.items()):
        for col, label in enumerate(sorted(labels_at_depth)):
            x = col * (_NODE_WIDTH + _COL_SPACING)
            y = depth * (_NODE_HEIGHT + _ROW_SPACING)
            canvas_nodes.append(_node_payload(label, x, y, nodes, frontier_reasons))

    canvas_edges: list[dict[str, Any]] = []
    for edge in valid_edges:
        canvas_edges.append(
            {
                "id": _edge_id(edge),
                "fromNode": _node_id(edge.subject),
                "toNode": _node_id(edge.object),
                "label": edge.predicate,
            }
        )

    canvas = {"nodes": canvas_nodes, "edges": canvas_edges}
    return json.dumps(canvas, indent=2, ensure_ascii=False)


def _node_id(label: str) -> str:
    """Stable node id — short hex hash of the label."""
    h = hashlib.sha1(label.encode("utf-8")).hexdigest()  # noqa: S324 — id only, not crypto
    return f"n-{h[:12]}"


def _edge_id(edge: StructuralEdge) -> str:
    key = f"{edge.subject}|{edge.predicate}|{edge.object}"
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()  # noqa: S324 — id only, not crypto
    return f"e-{h[:12]}"


def _node_payload(
    label: str,
    x: int,
    y: int,
    nodes: list[DisciplineNode],
    frontier_reasons: dict[str, str],
) -> dict[str, Any]:
    node = next((n for n in nodes if n.label == label), None)
    readiness = f"{node.readiness:.2f}" if node and node.readiness is not None else "—"
    text = f"{label}\n_readiness: {readiness}_"
    payload: dict[str, Any] = {
        "id": _node_id(label),
        "type": "text",
        "x": x,
        "y": y,
        "width": _NODE_WIDTH,
        "height": _NODE_HEIGHT,
        "text": text,
    }
    if label in frontier_reasons:
        reason = frontier_reasons[label]
        payload["color"] = _COLOR_BY_REASON.get(reason, "1")
    return payload


def _compute_depths(
    nodes: list[DisciplineNode],
    edges: list[StructuralEdge],
) -> dict[str, int]:
    """Compute each node's depth in the ``childOf`` hierarchy.

    Roots (no incoming ``childOf`` as subject) are depth 0; children sit one
    deeper than their parent. Non-``childOf`` edges don't contribute. Cycles
    are broken by tracking the visiting set.
    """
    parents: dict[str, list[str]] = {n.label: [] for n in nodes}
    for edge in edges:
        if edge.predicate == PRED_CHILD_OF:
            # subject childOf object → subject is child of object.
            parents[edge.subject].append(edge.object)

    depths: dict[str, int] = {}

    def depth_of(label: str, visiting: set[str]) -> int:
        if label in depths:
            return depths[label]
        if label in visiting:
            return 0
        if not parents.get(label):
            depths[label] = 0
            return 0
        visiting.add(label)
        parent_depth = max(depth_of(p, visiting) for p in parents[label])
        visiting.remove(label)
        d = parent_depth + 1
        depths[label] = d
        return d

    for n in nodes:
        depth_of(n.label, set())
    return depths
