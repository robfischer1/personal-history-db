"""Orchestrator — update one discipline's readiness end-to-end.

Reads the discipline's existing readiness state (asserted baseline +
last_verified), extracts practice events since then, runs the leaky
integrator, writes the result back as qualifiers, and reports the atrophy
alarm.

V1 note on ``delegation_recent``: the engine preserves the existing flag
but does not auto-update it. Detecting "Rob recently chose AI for this
discipline" requires a second pass over the AI-coauthored commits; deferred
to Phase 5 (pilot wiring).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import DisciplineNode
from .persistence import read_discipline, write_readiness
from .practice_events import (
    DisciplineMapper,
    default_discipline_mapper,
    extract_practice_events,
)
from .readiness import SkillGraphConfig, atrophy_alarm, compute_readiness, days_since


@dataclass(frozen=True)
class ReadinessUpdate:
    """The outcome of an ``update_discipline_readiness`` call."""

    discipline: str
    previous: DisciplineNode | None
    current: DisciplineNode
    atrophy_alarm: bool
    practice_event_count: int


def update_discipline_readiness(
    conn: sqlite3.Connection,
    discipline: str,
    *,
    repos: list[tuple[str, Path]],
    config: SkillGraphConfig,
    now: datetime | None = None,
    discipline_mapper: DisciplineMapper = default_discipline_mapper,
) -> ReadinessUpdate:
    """End-to-end readiness update for one discipline.

    1. Read existing state (asserted baseline + last_verified, if any).
    2. Extract practice events since ``last_verified`` (or full history if no
       prior baseline).
    3. Compute new readiness via the leaky integrator.
    4. Write back as qualifiers on ``(discipline, hasReadiness, NULL)``.
    5. Compute the atrophy alarm against the asserted baseline.
    """
    if now is None:
        now = datetime.now(UTC)
    now_iso = now.isoformat()

    previous = read_discipline(conn, discipline)
    asserted_baseline = previous.readiness if previous else None
    since_iso = previous.last_verified if previous and previous.last_verified else None

    events_by_discipline = extract_practice_events(
        repos,
        conn=conn,
        since_iso=since_iso,
        discipline_mapper=discipline_mapper,
    )
    events = events_by_discipline.get(discipline, [])

    tier_config = config.resolve_tier(discipline)
    event_ages = [days_since(e.timestamp, now=now) for e in events]
    days_since_baseline = (
        days_since(previous.last_verified, now=now)
        if previous and previous.last_verified
        else 0.0
    )

    new_value = compute_readiness(
        asserted_baseline=asserted_baseline,
        days_since_baseline=days_since_baseline,
        practice_event_ages=event_ages,
        tier=tier_config,
        boost_fraction=config.boost_fraction,
    )

    preserved_delegation = previous.delegation_recent if previous else False

    write_readiness(
        conn,
        discipline,
        value=new_value,
        last_verified=now_iso,
        delegation_recent=preserved_delegation,
        base_value=asserted_baseline,
        tier=tier_config.name,
    )

    current = DisciplineNode(
        label=discipline,
        readiness=new_value,
        last_verified=now_iso,
        delegation_recent=preserved_delegation,
    )

    alarm = False
    if asserted_baseline is not None:
        alarm = atrophy_alarm(
            asserted_baseline=asserted_baseline,
            days_since_baseline=days_since_baseline,
            tier=tier_config,
            alarm_fraction=config.alarm_fraction,
        )

    return ReadinessUpdate(
        discipline=discipline,
        previous=previous,
        current=current,
        atrophy_alarm=alarm,
        practice_event_count=len(events),
    )
