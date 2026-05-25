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
import math
from typing import Any

from .models import DisciplineNode, FrontierEntry, StructuralEdge
from .vocabulary import PRED_CHILD_OF

_NODE_WIDTH = 180  # -30% from 260 — tighter cards
_NODE_HEIGHT = 80

# Radial layout — Programming at center, categories on a ring, leaves on an outer ring.
_RADIAL_CENTER = (700, 700)
_R_CATEGORY = 260  # category-ring radius from center
_R_LEAF = 600  # leaf-ring radius from center
_INTER_CLUSTER_GAP_RAD = 0.18  # angular gap (radians) inserted between adjacent clusters

# Edge labels — empty by default; predicate type is signalled by color instead.
_EDGE_LABEL: dict[str, str] = {
    "prerequisiteOf": "",
    "childOf": "",
}

# Edge colors by predicate. JSON Canvas preset colors are "1"-"6".
# Prereq edges get a colored stroke; structural childOf edges stay default-gray.
_EDGE_COLOR_BY_PREDICATE: dict[str, str] = {
    "prerequisiteOf": "4",  # green — forward path: you build on this
}

# Obsidian Canvas preset colors (1-6). Used to highlight frontier nodes by reason.
_COLOR_BY_REASON: dict[str, str] = {
    "unaddressed": "5",  # purple — needs progress
    "under-informed": "3",  # yellow — needs re-probe
    "below-threshold": "2",  # red — addressed but sitting below the front; lift it
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

    positions = _compute_layout(nodes, valid_edges)

    canvas_nodes: list[dict[str, Any]] = []
    for label, (x, y) in positions.items():
        canvas_nodes.append(_node_payload(label, x, y, nodes, frontier_reasons))

    canvas_edges: list[dict[str, Any]] = []
    for edge in valid_edges:
        payload: dict[str, Any] = {
            "id": _edge_id(edge),
            "fromNode": _node_id(edge.subject),
            "toNode": _node_id(edge.object),
            "label": _EDGE_LABEL.get(edge.predicate, edge.predicate),
        }
        color = _EDGE_COLOR_BY_PREDICATE.get(edge.predicate)
        if color is not None:
            payload["color"] = color
        canvas_edges.append(payload)

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


def _compute_layout(
    nodes: list[DisciplineNode],
    edges: list[StructuralEdge],
) -> dict[str, tuple[int, int]]:
    """Radial (web) layout — root at center, categories on ring 1, leaves on ring 2.

    - Depth-0 nodes (root) sit at ``_RADIAL_CENTER``.
    - Depth-1 nodes (categories) are placed evenly around a circle of radius
      ``_R_CATEGORY`` from center.
    - Depth-2 nodes (leaves) fan out within their parent category's angular
      wedge at radius ``_R_LEAF``. Adjacent leaves alternate radii (push some
      slightly outward) so labels don't visually collide on the arc.
    - Anything deeper or unplaced falls back to a row beneath the diagram.
    """
    children_of: dict[str, list[str]] = {n.label: [] for n in nodes}
    for edge in edges:
        if edge.predicate == PRED_CHILD_OF:
            # subject childOf object → subject is child of object
            children_of[edge.object].append(edge.subject)
    for parent in children_of:
        children_of[parent].sort()

    depths = _compute_depths(nodes, edges)
    positions: dict[str, tuple[int, int]] = {}

    cx, cy = _RADIAL_CENTER
    depth_0 = sorted([lbl for lbl, d in depths.items() if d == 0])
    depth_1 = sorted([lbl for lbl, d in depths.items() if d == 1])

    # Roots at center
    for root in depth_0:
        positions[root] = (cx - _NODE_WIDTH // 2, cy - _NODE_HEIGHT // 2)

    # Global even leaf spacing: every leaf gets the same angular slot around
    # the circle. Each category sits at the angular center of its own children.
    # Small inter-cluster gaps separate one category's children from the next.
    n_cats = max(len(depth_1), 1)
    total_leaves = sum(len(children_of.get(c, [])) for c in depth_1)

    if total_leaves > 0:
        gap = _INTER_CLUSTER_GAP_RAD
        usable = 2 * math.pi - gap * n_cats
        step = usable / total_leaves
        cursor = -math.pi / 2 - usable / 2 - gap / 2  # start so the layout is top-centered
        for cat in depth_1:
            kids = children_of.get(cat, [])
            kid_angles: list[float] = []
            for kid in kids:
                # Center each leaf in its slot.
                theta = cursor + step / 2
                lx = cx + _R_LEAF * math.cos(theta) - _NODE_WIDTH // 2
                ly = cy + _R_LEAF * math.sin(theta) - _NODE_HEIGHT // 2
                positions[kid] = (int(lx), int(ly))
                kid_angles.append(theta)
                cursor += step
            cursor += gap  # gap before the next cluster begins

            # Category sits at the angular center of its children (or directly
            # at its own slot if it has no children, fallback to first slot).
            if kid_angles:
                theta_cat = sum(kid_angles) / len(kid_angles)
            else:
                theta_cat = -math.pi / 2  # top fallback
            cx_cat = cx + _R_CATEGORY * math.cos(theta_cat) - _NODE_WIDTH // 2
            cy_cat = cy + _R_CATEGORY * math.sin(theta_cat) - _NODE_HEIGHT // 2
            positions[cat] = (int(cx_cat), int(cy_cat))
    else:
        # Fall back to fixed even spacing if no leaves are present yet.
        for i, cat in enumerate(depth_1):
            theta = -math.pi / 2 + i * (2 * math.pi / n_cats)
            x = cx + _R_CATEGORY * math.cos(theta) - _NODE_WIDTH // 2
            y = cy + _R_CATEGORY * math.sin(theta) - _NODE_HEIGHT // 2
            positions[cat] = (int(x), int(y))

    # Fallback for unplaced nodes — single row beneath everything.
    placed = set(positions.keys())
    unplaced = [n.label for n in nodes if n.label not in placed]
    if unplaced:
        fallback_y = cy + _R_LEAF + _LEAF_RADIAL_JITTER + _NODE_HEIGHT * 2
        col_step = _NODE_WIDTH + 60
        for i, label in enumerate(sorted(unplaced)):
            positions[label] = (i * col_step, fallback_y)

    return positions


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
