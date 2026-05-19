"""Skill-graph readiness engine — leaky integrator over discipline practice events.

Reuses the decay mechanics from ``phdb.scoring`` (lambda from half-life,
exponential decay) but applies a discipline-specific interpretation:

- ``asserted_baseline`` is the readiness Rob (or AI proposing, Rob confirming)
  last manually anchored at a given date. It decays from there.
- ``practice_event_ages`` are days-since each *rob-authored* commit (or any
  unaided practice event) — each contributes a ``boost_fraction`` of base,
  also decaying with the tier's half-life.
- The floor is a fraction of the *asserted baseline* (not a static tier
  base), so readiness can't collapse below "Rob asserted this much at some
  point."

The atrophy alarm fires when the no-practice projection drops below
``alarm_fraction`` of the asserted baseline — the anti-atrophy decay floor
from Skill Graph D10.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from phdb.scoring import TierConfig, decay_factor

_DEFAULT_CONFIG = Path(__file__).parents[3] / "config" / "skill_graph.toml"


@dataclass(frozen=True)
class SkillGraphConfig:
    """Tunable parameters for the skill-graph readiness engine."""

    tiers: dict[str, TierConfig]
    boost_fraction: float
    alarm_fraction: float
    discipline_tiers: dict[str, str]  # discipline label → tier name

    @classmethod
    def load(cls, config_path: Path | None = None) -> SkillGraphConfig:
        path = config_path or _DEFAULT_CONFIG
        with open(path, "rb") as f:
            raw = tomllib.load(f)

        tiers: dict[str, TierConfig] = {}
        for name, cfg in raw.get("tiers", {}).items():
            tiers[name] = TierConfig(
                name=name,
                half_life_days=cfg["half_life_days"],
                base_value=cfg["base_value"],
                floor_fraction=cfg["floor_fraction"],
            )

        return cls(
            tiers=tiers,
            boost_fraction=raw.get("engagement", {}).get("boost_fraction", 0.05),
            alarm_fraction=raw.get("atrophy", {}).get("alarm_fraction", 0.5),
            discipline_tiers=raw.get("discipline_tiers", {}),
        )

    def resolve_tier(
        self,
        discipline_label: str,
        tier_override: str | None = None,
    ) -> TierConfig:
        """Return the TierConfig for a discipline.

        Resolution order: explicit override → discipline_tiers mapping →
        ``active`` default → first available → hardcoded last-resort.
        """
        if tier_override and tier_override in self.tiers:
            return self.tiers[tier_override]
        mapped = self.discipline_tiers.get(discipline_label)
        if mapped and mapped in self.tiers:
            return self.tiers[mapped]
        if "active" in self.tiers:
            return self.tiers["active"]
        if self.tiers:
            return next(iter(self.tiers.values()))
        return TierConfig(
            name="default",
            half_life_days=180.0,
            base_value=1.0,
            floor_fraction=0.05,
        )


def compute_readiness(
    *,
    asserted_baseline: float | None,
    days_since_baseline: float,
    practice_event_ages: list[float],
    tier: TierConfig,
    boost_fraction: float = 0.05,
) -> float | None:
    """Leaky integrator over practice events for one discipline.

    Returns the current readiness estimate in ``[floor, 1.0]``, or ``None`` if
    there is no signal (no asserted baseline and no practice events).

    Formula::

        readiness = clamp(
            floor,
            asserted_baseline * decay(days_since_baseline)
              + Σ (boost_fraction * decay(event_age_days)),
            1.0,
        )

    The floor is ``asserted_baseline * tier.floor_fraction`` when a baseline
    exists, else 0.0 (no baseline anchor, no floor anchoring).
    """
    if asserted_baseline is None and not practice_event_ages:
        return None

    base_component = 0.0
    floor = 0.0
    if asserted_baseline is not None:
        base_component = asserted_baseline * decay_factor(days_since_baseline, tier.lambda_)
        floor = asserted_baseline * tier.floor_fraction

    boost_sum = sum(
        boost_fraction * decay_factor(age, tier.lambda_) for age in practice_event_ages
    )
    raw = base_component + boost_sum
    return min(1.0, max(floor, raw))


def predict_decay(
    *,
    asserted_baseline: float,
    days_since_baseline: float,
    tier: TierConfig,
) -> float:
    """Predicted readiness right now, ignoring any practice boosts.

    Used by the atrophy alarm: this is the readiness Rob has *if* he hasn't
    practiced since the baseline was asserted. Floor-anchored to
    ``asserted_baseline * tier.floor_fraction``.
    """
    decayed = asserted_baseline * decay_factor(days_since_baseline, tier.lambda_)
    floor = asserted_baseline * tier.floor_fraction
    return max(floor, decayed)


def atrophy_alarm(
    *,
    asserted_baseline: float,
    days_since_baseline: float,
    tier: TierConfig,
    alarm_fraction: float,
) -> bool:
    """True when the no-practice projection has dropped past the alarm threshold.

    Implements Skill Graph D10's anti-atrophy decay floor: every mastered
    node gets re-probed before its predicted decay crosses this threshold.
    """
    predicted = predict_decay(
        asserted_baseline=asserted_baseline,
        days_since_baseline=days_since_baseline,
        tier=tier,
    )
    return predicted < asserted_baseline * alarm_fraction


def days_since(iso_ts: str, now: datetime | None = None) -> float:
    """Days between an ISO 8601 timestamp and now (UTC).

    Naive timestamps are interpreted as UTC. Returns 0.0 for malformed input.
    """
    if now is None:
        now = datetime.now(UTC)
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return max(0.0, (now - ts).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 0.0
