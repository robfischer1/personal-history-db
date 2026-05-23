"""Tests for phdb.skill_graph.engine — orchestrator integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from phdb.core.scoring import TierConfig
from phdb.db import connect
from phdb.skill_graph import engine
from phdb.skill_graph.engine import update_discipline_readiness
from phdb.skill_graph.persistence import write_readiness
from phdb.skill_graph.practice_events import PracticeEvent
from phdb.skill_graph.readiness import SkillGraphConfig


@pytest.fixture
def small_config() -> SkillGraphConfig:
    return SkillGraphConfig(
        tiers={
            "active": TierConfig(
                name="active",
                half_life_days=180.0,
                base_value=1.0,
                floor_fraction=0.05,
            ),
        },
        boost_fraction=0.05,
        alarm_fraction=0.5,
        discipline_tiers={"Programming": "active"},
    )


def _stub_extract(
    events_by_discipline: dict[str, list[PracticeEvent]],
) -> Callable[..., dict[str, list[PracticeEvent]]]:
    def _stub(*_args: object, **_kwargs: object) -> dict[str, list[PracticeEvent]]:
        return events_by_discipline

    return _stub


def test_engine_first_run_with_events(
    migrated_db: Path,
    small_config: SkillGraphConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First update with practice events but no prior baseline."""
    events = {
        "Programming": [
            PracticeEvent(
                timestamp="2026-05-18T12:00:00Z",
                discipline="Programming",
                repo="vault",
                sha="aaa",
            ),
            PracticeEvent(
                timestamp="2026-05-19T12:00:00Z",
                discipline="Programming",
                repo="vault",
                sha="bbb",
            ),
        ]
    }
    monkeypatch.setattr(engine, "extract_practice_events", _stub_extract(events))

    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    with connect(migrated_db) as conn:
        result = update_discipline_readiness(
            conn,
            "Programming",
            repos=[],
            config=small_config,
            now=now,
        )

    assert result.discipline == "Programming"
    assert result.previous is None
    assert result.current.readiness is not None
    assert result.current.readiness > 0
    assert result.practice_event_count == 2
    assert result.atrophy_alarm is False


def test_engine_persists_readiness_for_subsequent_read(
    migrated_db: Path,
    small_config: SkillGraphConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An engine run writes back state that read_discipline can recover."""
    events = {
        "Programming": [
            PracticeEvent(
                timestamp="2026-05-19T00:00:00Z",
                discipline="Programming",
                repo="vault",
                sha="aaa",
            )
        ]
    }
    monkeypatch.setattr(engine, "extract_practice_events", _stub_extract(events))

    with connect(migrated_db) as conn:
        update_discipline_readiness(
            conn, "Programming", repos=[], config=small_config,
        )

        from phdb.skill_graph.persistence import read_discipline

        node = read_discipline(conn, "Programming")
        assert node is not None
        assert node.readiness is not None
        assert node.last_verified is not None


def test_engine_carries_prior_baseline(
    migrated_db: Path,
    small_config: SkillGraphConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing baseline is read and used as the baseline for the next run."""
    monkeypatch.setattr(engine, "extract_practice_events", _stub_extract({}))

    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    with connect(migrated_db) as conn:
        write_readiness(
            conn,
            "Programming",
            value=0.6,
            last_verified="2026-05-01T00:00:00Z",
        )

        result = update_discipline_readiness(
            conn, "Programming", repos=[], config=small_config, now=now,
        )

    assert result.previous is not None
    assert result.previous.readiness == pytest.approx(0.6, abs=0.001)
    # ~18 days of decay, half_life=180 → very slight drop from 0.6.
    assert result.current.readiness is not None
    assert result.current.readiness < 0.6
    assert result.current.readiness > 0.55


def test_engine_atrophy_alarm_fires_when_stale(
    migrated_db: Path,
    small_config: SkillGraphConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long-stale baseline with no new events triggers the atrophy alarm."""
    monkeypatch.setattr(engine, "extract_practice_events", _stub_extract({}))

    now = datetime(2027, 5, 19, 12, 0, 0, tzinfo=UTC)  # ~1 year later
    with connect(migrated_db) as conn:
        write_readiness(
            conn,
            "Programming",
            value=0.8,
            last_verified="2026-05-19T00:00:00Z",
        )

        result = update_discipline_readiness(
            conn, "Programming", repos=[], config=small_config, now=now,
        )

    # ~365 days since baseline, half_life=180 → projected ≈ 0.2,
    # which is below 0.5 * 0.8 = 0.4. Alarm should fire.
    assert result.atrophy_alarm is True


def test_engine_no_alarm_without_baseline(
    migrated_db: Path,
    small_config: SkillGraphConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "extract_practice_events", _stub_extract({}))

    with connect(migrated_db) as conn:
        result = update_discipline_readiness(
            conn, "Programming", repos=[], config=small_config,
        )

    assert result.previous is None
    assert result.atrophy_alarm is False
