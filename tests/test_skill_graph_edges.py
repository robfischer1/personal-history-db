"""Tests for phdb.skill_graph.edges — action-edge generation via provider."""

from __future__ import annotations

from phdb.skill_graph import ActionEdge, DisciplineNode, FrontierEntry
from phdb.skill_graph.edges import ActionProvider, generate_actions
from phdb.skill_graph.models import FrontierReason


def _make_provider(actions_by_label: dict[str, list[ActionEdge]]) -> ActionProvider:
    def provider(label: str, _reason: FrontierReason) -> list[ActionEdge]:
        return list(actions_by_label.get(label, []))

    return provider


def test_generate_actions_attaches_provider_output() -> None:
    entries = [FrontierEntry(node=DisciplineNode(label="Spanish"), reason="unaddressed")]
    actions = [
        ActionEdge(
            kind="progress",
            target="Spanish",
            description="Pimsleur lesson 1",
            est_value=0.7,
            cost=0.2,
            novelty=0.5,
        )
    ]

    enriched = generate_actions(entries, action_provider=_make_provider({"Spanish": actions}))

    assert len(enriched) == 1
    assert len(enriched[0].actions) == 1
    assert enriched[0].actions[0].description == "Pimsleur lesson 1"


def test_generate_actions_handles_empty_provider() -> None:
    entries = [FrontierEntry(node=DisciplineNode(label="UnknownDiscipline"), reason="unaddressed")]
    enriched = generate_actions(entries, action_provider=_make_provider({}))
    assert enriched[0].actions == []


def test_generate_actions_preserves_node_and_reason() -> None:
    node = DisciplineNode(label="Python", readiness=0.7, last_verified="2026-01-01T00:00:00")
    entries = [FrontierEntry(node=node, reason="under-informed")]
    actions = [
        ActionEdge(
            kind="quantify",
            target="Python",
            description="Do a kata, no AI",
            est_value=0.8,
            cost=0.1,
            novelty=0.6,
        )
    ]

    enriched = generate_actions(entries, action_provider=_make_provider({"Python": actions}))

    assert enriched[0].node == node
    assert enriched[0].reason == "under-informed"


def test_provider_can_branch_on_reason() -> None:
    """Provider receives the FrontierReason — can return different actions per state."""

    def reason_aware(label: str, reason: FrontierReason) -> list[ActionEdge]:
        if reason == "unaddressed":
            return [
                ActionEdge(
                    kind="progress",
                    target=label,
                    description="Start",
                    est_value=0.5,
                    cost=0.3,
                    novelty=0.4,
                )
            ]
        return [
            ActionEdge(
                kind="quantify",
                target=label,
                description="Re-probe",
                est_value=0.4,
                cost=0.1,
                novelty=0.2,
            )
        ]

    entries = [
        FrontierEntry(node=DisciplineNode(label="A"), reason="unaddressed"),
        FrontierEntry(node=DisciplineNode(label="B", readiness=0.6), reason="under-informed"),
    ]
    enriched = generate_actions(entries, action_provider=reason_aware)

    assert enriched[0].actions[0].kind == "progress"
    assert enriched[1].actions[0].kind == "quantify"
