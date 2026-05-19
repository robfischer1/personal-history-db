"""Tests for phdb.skill_graph.canvas — JSON Canvas rendering."""

from __future__ import annotations

import json
from typing import Any

from phdb.skill_graph import DisciplineNode, FrontierEntry, StructuralEdge
from phdb.skill_graph.canvas import render_canvas


def _parse(out: str) -> dict[str, Any]:
    return json.loads(out)  # type: ignore[no-any-return]


def test_canvas_is_valid_json() -> None:
    nodes = [DisciplineNode(label="A"), DisciplineNode(label="B")]
    edges = [StructuralEdge("A", "prerequisiteOf", "B")]
    parsed = _parse(render_canvas(nodes, edges))
    assert "nodes" in parsed
    assert "edges" in parsed


def test_canvas_emits_one_node_per_discipline() -> None:
    nodes = [DisciplineNode(label="A"), DisciplineNode(label="B"), DisciplineNode(label="C")]
    parsed = _parse(render_canvas(nodes, []))
    assert len(parsed["nodes"]) == 3


def test_canvas_node_payload_shape() -> None:
    """Each node has the JSON Canvas required keys."""
    nodes = [DisciplineNode(label="A", readiness=0.5, last_verified="2026-05-19")]
    parsed = _parse(render_canvas(nodes, []))
    n = parsed["nodes"][0]
    for key in ("id", "type", "x", "y", "width", "height", "text"):
        assert key in n, f"missing key {key}"
    assert n["type"] == "text"
    assert "A" in n["text"]
    assert "0.50" in n["text"]


def test_canvas_drops_edges_with_missing_nodes() -> None:
    nodes = [DisciplineNode(label="A")]
    edges = [
        StructuralEdge("A", "prerequisiteOf", "B"),  # B missing → dropped
        StructuralEdge("A", "childOf", "A"),  # both endpoints present
    ]
    parsed = _parse(render_canvas(nodes, edges))
    assert len(parsed["edges"]) == 1


def test_canvas_highlights_frontier_nodes_only() -> None:
    nodes = [
        DisciplineNode(label="A", readiness=0.7, last_verified="2026-05-15"),
        DisciplineNode(label="B"),
    ]
    frontier = [FrontierEntry(node=nodes[1], reason="unaddressed", actions=[])]
    parsed = _parse(render_canvas(nodes, [], frontier=frontier))

    by_label = {n["text"].split("\n")[0]: n for n in parsed["nodes"]}
    assert "color" in by_label["B"]  # Frontier node → colored
    assert "color" not in by_label["A"]  # Not on frontier


def test_canvas_color_differs_by_reason() -> None:
    nodes = [DisciplineNode(label="A"), DisciplineNode(label="B")]
    frontier = [
        FrontierEntry(node=nodes[0], reason="unaddressed"),
        FrontierEntry(node=nodes[1], reason="under-informed"),
    ]
    parsed = _parse(render_canvas(nodes, [], frontier=frontier))
    by_label = {n["text"].split("\n")[0]: n for n in parsed["nodes"]}
    assert by_label["A"]["color"] != by_label["B"]["color"]


def test_canvas_layout_uses_childof_depth() -> None:
    nodes = [
        DisciplineNode(label="Programming"),
        DisciplineNode(label="JS"),
        DisciplineNode(label="React"),
    ]
    edges = [
        StructuralEdge("JS", "childOf", "Programming"),
        StructuralEdge("React", "childOf", "JS"),
    ]
    parsed = _parse(render_canvas(nodes, edges))
    by_label = {n["text"].split("\n")[0]: n for n in parsed["nodes"]}
    # Programming at depth 0, JS at depth 1, React at depth 2.
    assert by_label["Programming"]["y"] < by_label["JS"]["y"]
    assert by_label["JS"]["y"] < by_label["React"]["y"]


def test_canvas_edges_carry_predicate_label() -> None:
    nodes = [DisciplineNode(label="A"), DisciplineNode(label="B")]
    edges = [StructuralEdge("A", "prerequisiteOf", "B")]
    parsed = _parse(render_canvas(nodes, edges))
    assert parsed["edges"][0]["label"] == "prerequisiteOf"


def test_canvas_empty_inputs() -> None:
    parsed = _parse(render_canvas([], []))
    assert parsed == {"nodes": [], "edges": []}
