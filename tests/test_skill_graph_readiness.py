"""Tests for phdb.skill_graph.readiness — the leaky integrator math."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from phdb.scoring import TierConfig
from phdb.skill_graph.readiness import (
    SkillGraphConfig,
    atrophy_alarm,
    compute_readiness,
    days_since,
    predict_decay,
)


def _tier(
    *,
    half_life_days: float = 180.0,
    base: float = 1.0,
    floor_frac: float = 0.05,
) -> TierConfig:
    return TierConfig(
        name="test",
        half_life_days=half_life_days,
        base_value=base,
        floor_fraction=floor_frac,
    )


# === compute_readiness ===


def test_compute_readiness_returns_none_with_no_signal() -> None:
    assert (
        compute_readiness(
            asserted_baseline=None,
            days_since_baseline=0.0,
            practice_event_ages=[],
            tier=_tier(),
        )
        is None
    )


def test_compute_readiness_fresh_baseline_no_events_returns_baseline() -> None:
    """Day 0 of the baseline with no events: readiness == baseline."""
    out = compute_readiness(
        asserted_baseline=0.7,
        days_since_baseline=0.0,
        practice_event_ages=[],
        tier=_tier(),
    )
    assert out == pytest.approx(0.7)


def test_compute_readiness_decays_baseline_one_half_life() -> None:
    """After one half-life with no practice: baseline ≈ halves (above floor)."""
    out = compute_readiness(
        asserted_baseline=0.8,
        days_since_baseline=180.0,
        practice_event_ages=[],
        tier=_tier(half_life_days=180.0, floor_frac=0.05),
    )
    assert out == pytest.approx(0.4, abs=0.01)


def test_compute_readiness_floor_anchored_to_baseline() -> None:
    """Past many half-lives with no practice, readiness floors at baseline*floor_fraction."""
    out = compute_readiness(
        asserted_baseline=0.6,
        days_since_baseline=10_000.0,
        practice_event_ages=[],
        tier=_tier(half_life_days=180.0, floor_frac=0.05),
    )
    # Floor = 0.6 * 0.05 = 0.03
    assert out == pytest.approx(0.03, abs=0.001)


def test_compute_readiness_boost_from_practice_events() -> None:
    """Each recent practice event adds boost_fraction * decay(age)."""
    out_no_practice = compute_readiness(
        asserted_baseline=0.5,
        days_since_baseline=180.0,
        practice_event_ages=[],
        tier=_tier(half_life_days=180.0),
        boost_fraction=0.05,
    )
    out_with_practice = compute_readiness(
        asserted_baseline=0.5,
        days_since_baseline=180.0,
        practice_event_ages=[0.0, 0.0, 0.0],  # 3 fresh events
        tier=_tier(half_life_days=180.0),
        boost_fraction=0.05,
    )
    # Fresh events each add 0.05 → 3 * 0.05 = 0.15 above the no-practice case.
    assert out_with_practice > out_no_practice  # type: ignore[operator]
    assert out_with_practice - out_no_practice == pytest.approx(0.15, abs=0.001)  # type: ignore[operator]


def test_compute_readiness_caps_at_one() -> None:
    out = compute_readiness(
        asserted_baseline=0.9,
        days_since_baseline=0.0,
        practice_event_ages=[0.0] * 100,
        tier=_tier(),
        boost_fraction=0.1,
    )
    assert out == 1.0


def test_compute_readiness_no_baseline_with_practice() -> None:
    """No asserted baseline but practice events exist — readiness from boosts alone."""
    out = compute_readiness(
        asserted_baseline=None,
        days_since_baseline=0.0,
        practice_event_ages=[0.0, 0.0],
        tier=_tier(),
        boost_fraction=0.05,
    )
    assert out == pytest.approx(0.1, abs=0.001)


# === predict_decay ===


def test_predict_decay_at_day_zero_returns_baseline() -> None:
    assert predict_decay(asserted_baseline=0.7, days_since_baseline=0.0, tier=_tier()) == pytest.approx(0.7)


def test_predict_decay_one_half_life_halves_baseline() -> None:
    out = predict_decay(
        asserted_baseline=0.8,
        days_since_baseline=180.0,
        tier=_tier(half_life_days=180.0),
    )
    assert out == pytest.approx(0.4, abs=0.01)


def test_predict_decay_floors_at_baseline_floor_fraction() -> None:
    out = predict_decay(
        asserted_baseline=1.0,
        days_since_baseline=10_000.0,
        tier=_tier(floor_frac=0.05),
    )
    assert out == pytest.approx(0.05, abs=0.001)


# === atrophy_alarm ===


def test_atrophy_alarm_quiet_at_baseline() -> None:
    assert (
        atrophy_alarm(
            asserted_baseline=0.8,
            days_since_baseline=0.0,
            tier=_tier(half_life_days=180.0),
            alarm_fraction=0.5,
        )
        is False
    )


def test_atrophy_alarm_silent_before_half_life() -> None:
    assert (
        atrophy_alarm(
            asserted_baseline=0.8,
            days_since_baseline=90.0,
            tier=_tier(half_life_days=180.0),
            alarm_fraction=0.5,
        )
        is False
    )


def test_atrophy_alarm_fires_past_half_life() -> None:
    """Just past one half-life: predicted ≈ 0.5*baseline. alarm_fraction=0.5 → fires."""
    assert (
        atrophy_alarm(
            asserted_baseline=0.8,
            days_since_baseline=181.0,
            tier=_tier(half_life_days=180.0),
            alarm_fraction=0.5,
        )
        is True
    )


# === days_since ===


def test_days_since_zero_for_now() -> None:
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    assert days_since("2026-05-19T12:00:00", now=now) == 0.0


def test_days_since_one_day_later() -> None:
    now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
    assert days_since("2026-05-19T12:00:00", now=now) == pytest.approx(1.0)


def test_days_since_malformed_returns_zero() -> None:
    assert days_since("not-a-date") == 0.0


def test_days_since_handles_utc_suffix() -> None:
    now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
    assert days_since("2026-05-19T12:00:00Z", now=now) == pytest.approx(1.0)


# === SkillGraphConfig ===


def test_skill_graph_config_loads_default() -> None:
    config = SkillGraphConfig.load()
    assert "active" in config.tiers
    assert config.boost_fraction > 0
    assert 0 < config.alarm_fraction <= 1
    assert "Programming" in config.discipline_tiers


def test_skill_graph_config_resolves_tier_by_discipline() -> None:
    config = SkillGraphConfig.load()
    tier = config.resolve_tier("Programming")
    assert tier.name == "active"


def test_skill_graph_config_resolves_tier_fallback() -> None:
    config = SkillGraphConfig.load()
    tier = config.resolve_tier("UnknownDiscipline")
    # Falls back to "active" default.
    assert tier.name == "active"


def test_skill_graph_config_override_wins() -> None:
    config = SkillGraphConfig.load()
    tier = config.resolve_tier("Programming", tier_override="foundation")
    assert tier.name == "foundation"
