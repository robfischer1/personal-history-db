"""Render the skill-graph frontier as a markdown digest — Rob's primary text surface.

The digest lists each frontier node (with its `reason`: unaddressed or
under-informed) and its top ranked next-actions. Zoom (D8) translates here
as `max_actions_per_node` — coarse output uses small N, fine output larger.
"""

from __future__ import annotations

from .models import FrontierEntry


def render_digest(
    entries: list[FrontierEntry],
    *,
    max_actions_per_node: int = 3,
    heading_level: int = 2,
) -> str:
    """Return a markdown string suitable for writing to a vault note.

    Args:
        entries: Ranked frontier entries (run them through
            `ranker.rank_actions` first).
        max_actions_per_node: How many top actions to show per node.
        heading_level: Starting heading depth (2 = ``##`` for the title,
            ``###`` per node).
    """
    h = "#" * heading_level
    h_sub = "#" * (heading_level + 1)

    if not entries:
        return f"{h} Skill-graph frontier\n\n_Nothing on the frontier — everything addressed and fresh._\n"

    lines: list[str] = [
        f"{h} Skill-graph frontier",
        "",
        f"_{len(entries)} discipline{'s' if len(entries) != 1 else ''} on the frontier._",
        "",
    ]

    for entry in entries:
        node = entry.node
        readiness_str = f"{node.readiness:.2f}" if node.readiness is not None else "—"
        delegation_note = " · *delegation recent*" if node.delegation_recent else ""
        last_verified_str = node.last_verified if node.last_verified else "never"

        lines.append(f"{h_sub} {node.label} — *{entry.reason}*")
        lines.append("")
        lines.append(
            f"- Readiness: `{readiness_str}` · last verified `{last_verified_str}`{delegation_note}"
        )

        if entry.actions:
            lines.append("- Next actions (ranked):")
            for action in entry.actions[:max_actions_per_node]:
                lines.append(
                    f"    - **{action.kind}** — {action.description} "
                    f"(score `{action.score:.2f}` · value `{action.est_value:.2f}` "
                    f"· cost `{action.cost:.2f}` · novelty `{action.novelty:.2f}` "
                    f"· P(act) `{action.p_action:.2f}`)"
                )
        else:
            lines.append("- _No actions available._")

        lines.append("")

    return "\n".join(lines)
