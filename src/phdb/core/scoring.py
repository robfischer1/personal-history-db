"""Decay scoring engine — leaky integrator over chunk retrieval weights.

Moved here from ``phdb.scoring`` as part of Phase 1 of the phdb Plugin
Architecture plan. The legacy module re-exports from this location.

Phase 1 refactor: the previously-hardcoded ``_DATE_COLUMN`` dict is
sourced from ``phdb.core.registry.default_registry()``. Phase 3 will
populate that registry from plugin manifests; this module's surface
stays the same.

Implements the decay policy: every chunk has a score that decays with age,
refreshes on engagement, and varies by content class. The score affects
retrieval ranking but never causes deletion.

Formula:
    score = max(floor, base * decay(age) + Σ boost * decay(time_since_engagement_i))
    decay(t) = e^(-λ * t_days)  where λ = ln(2) / half_life_days

Tiers control base_value and half_life. Engagement events inject energy
that decays independently from their own timestamp.
"""

from __future__ import annotations

import math
import sqlite3
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phdb.core.registry import Registry, default_registry
from phdb.log import get_logger

log = get_logger("phdb.scoring")

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "decay_policy.toml"


@dataclass(frozen=True)
class TierConfig:
    name: str
    half_life_days: float
    base_value: float
    floor_fraction: float

    @property
    def floor(self) -> float:
        return self.base_value * self.floor_fraction

    @property
    def lambda_(self) -> float:
        if self.half_life_days <= 0:
            return 0.0
        return math.log(2) / self.half_life_days


@dataclass(frozen=True)
class DecayConfig:
    tiers: dict[str, TierConfig]
    source_kind_tiers: dict[str, str]
    schema_type_overrides: dict[str, str]
    boost_fraction: float
    health_recency_window_days: int

    @classmethod
    def load(cls, config_path: Path | None = None) -> DecayConfig:
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
            source_kind_tiers=raw.get("source_kind_tiers", {}),
            schema_type_overrides=raw.get("schema_type_overrides", {}),
            boost_fraction=raw.get("engagement", {}).get("boost_fraction", 0.1),
            health_recency_window_days=raw.get("health_floor", {}).get(
                "recency_window_days", 30
            ),
        )

    def resolve_tier(
        self,
        source_kind: str | None,
        schema_type: str | None,
        tier_override: str | None = None,
    ) -> TierConfig:
        if tier_override and tier_override in self.tiers:
            return self.tiers[tier_override]
        if schema_type and schema_type in self.schema_type_overrides:
            tier_name = self.schema_type_overrides[schema_type]
            if tier_name in self.tiers:
                return self.tiers[tier_name]
        if source_kind and source_kind in self.source_kind_tiers:
            tier_name = self.source_kind_tiers[source_kind]
            if tier_name in self.tiers:
                return self.tiers[tier_name]
        return self.tiers.get("standard", TierConfig("standard", 730, 1.0, 0.05))


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _days_between(iso_ts: str, now: datetime) -> float:
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta = now - ts
        return max(0.0, delta.total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 0.0


def decay_factor(age_days: float, lambda_: float) -> float:
    if lambda_ <= 0:
        return 1.0
    return math.exp(-lambda_ * age_days)


def compute_score(
    tier: TierConfig,
    age_days: float,
    engagements: list[float],
    boost_fraction: float,
) -> float:
    base_decay = tier.base_value * decay_factor(age_days, tier.lambda_)
    boost_sum = sum(
        tier.base_value * boost_fraction * decay_factor(eng_age, tier.lambda_)
        for eng_age in engagements
    )
    raw = base_decay + boost_sum
    return max(tier.floor, raw)


def resolve_source_kind(
    conn: sqlite3.Connection,
    chunk_id: int,
    source_table: str,
    source_id: int,
) -> str | None:
    if source_table == "documents":
        row = conn.execute(
            "SELECT sf.source_kind FROM documents d"
            " JOIN source_files sf ON sf.id = d.source_file_id"
            " WHERE d.id = ?",
            (source_id,),
        ).fetchone()
    else:
        # source_table is the actual typed table name (emails, chat_messages, etc.)
        # All typed tables have source_file_id.
        row = conn.execute(
            f"SELECT sf.source_kind FROM [{source_table}] m"
            f" JOIN source_files sf ON sf.id = m.source_file_id"
            f" WHERE m.id = ?",
            (source_id,),
        ).fetchone()
    return row[0] if row else None


def _resolve_content_date(
    conn: sqlite3.Connection,
    source_table: str,
    source_id: int,
    content_date_cache: dict[tuple[str, int], str],
    *,
    registry: Registry | None = None,
) -> str:
    """Get the actual content date (not ingestion date) for a chunk's parent row."""
    cache_key = (source_table, source_id)
    if cache_key in content_date_cache:
        return content_date_cache[cache_key]

    date_val = ""
    if source_table == "documents":
        row = conn.execute(
            "SELECT COALESCE(mtime, ctime, created_at) FROM documents WHERE id = ?",
            (source_id,),
        ).fetchone()
        if row:
            date_val = row[0] or ""
    else:
        reg = registry or default_registry()
        date_col = reg.date_column(source_table, default="date_sent")
        row = conn.execute(
            f"SELECT [{date_col}] FROM [{source_table}] WHERE id = ?",
            (source_id,),
        ).fetchone()
        if row:
            date_val = row[0] or ""

    content_date_cache[cache_key] = date_val
    return date_val


def populate_initial_scores(
    conn: sqlite3.Connection,
    config: DecayConfig | None = None,
    batch_size: int = 5000,
    *,
    registry: Registry | None = None,
) -> int:
    """Compute initial scores for all chunks not yet in chunk_scores.

    Uses base * decay(age) only — no engagements exist yet.
    Age is derived from the content's actual date (typed-table date column or
    documents.mtime), NOT chunks.created_at (which is ingestion timestamp).
    Returns count of rows inserted.
    """
    if config is None:
        config = DecayConfig.load()
    reg = registry or default_registry()

    now = _now_utc()
    total = 0

    source_kind_cache: dict[tuple[str, int], str | None] = {}
    content_date_cache: dict[tuple[str, int], str] = {}

    while True:
        rows = conn.execute(
            "SELECT c.id, c.source_table, c.source_id, c.schema_type"
            " FROM chunks c"
            " LEFT JOIN chunk_scores cs ON cs.chunk_id = c.id"
            " WHERE cs.chunk_id IS NULL"
            " ORDER BY c.id"
            " LIMIT ?",
            (batch_size,),
        ).fetchall()

        if not rows:
            break

        insert_batch: list[tuple[Any, ...]] = []

        for row in rows:
            chunk_id = row[0]
            source_table = row[1]
            source_id = row[2]
            schema_type = row[3]

            cache_key = (source_table, source_id)
            if cache_key not in source_kind_cache:
                source_kind_cache[cache_key] = resolve_source_kind(
                    conn, chunk_id, source_table, source_id
                )
            source_kind = source_kind_cache[cache_key]

            content_date = _resolve_content_date(
                conn, source_table, source_id, content_date_cache, registry=reg
            )

            tier = config.resolve_tier(source_kind, schema_type)
            age_days = _days_between(content_date, now) if content_date else 0.0
            score = compute_score(tier, age_days, [], config.boost_fraction)

            insert_batch.append((
                chunk_id,
                score,
                tier.name,
                tier.base_value,
                now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            ))

        conn.executemany(
            "INSERT OR IGNORE INTO chunk_scores"
            " (chunk_id, score, tier, base_value, last_recomputed)"
            " VALUES (?, ?, ?, ?, ?)",
            insert_batch,
        )
        conn.commit()
        total += len(insert_batch)
        log.info("Populated %d chunk scores so far", total)

    log.info("Initial population complete: %d chunks scored", total)
    return total


def batch_recompute(
    conn: sqlite3.Connection,
    config: DecayConfig | None = None,
    tier_filter: str | None = None,
    *,
    registry: Registry | None = None,
) -> int:
    """Recompute scores for all (or one tier of) chunks.

    Reads engagement history for each chunk and applies the full formula.
    Age is derived from content date (typed-table date column / documents.mtime),
    not chunks.created_at.
    Returns count of rows updated.
    """
    if config is None:
        config = DecayConfig.load()
    reg = registry or default_registry()

    now = _now_utc()
    total = 0

    where = "WHERE cs.tier = ?" if tier_filter else ""
    params: tuple[Any, ...] = (tier_filter,) if tier_filter else ()

    rows = conn.execute(
        f"SELECT cs.chunk_id, cs.tier, cs.tier_override,"
        f" c.source_table, c.source_id, c.schema_type"
        f" FROM chunk_scores cs"
        f" JOIN chunks c ON c.id = cs.chunk_id"
        f" {where}",
        params,
    ).fetchall()

    source_kind_cache: dict[tuple[str, int], str | None] = {}
    content_date_cache: dict[tuple[str, int], str] = {}
    updates: list[tuple[Any, ...]] = []

    for row in rows:
        chunk_id = row[0]
        tier_override = row[2]
        source_table = row[3]
        source_id = row[4]
        schema_type = row[5]

        cache_key = (source_table, source_id)
        if cache_key not in source_kind_cache:
            source_kind_cache[cache_key] = resolve_source_kind(
                conn, chunk_id, source_table, source_id
            )
        source_kind = source_kind_cache[cache_key]

        content_date = _resolve_content_date(
            conn, source_table, source_id, content_date_cache, registry=reg
        )

        tier = config.resolve_tier(source_kind, schema_type, tier_override)
        age_days = _days_between(content_date, now) if content_date else 0.0

        eng_rows = conn.execute(
            "SELECT timestamp FROM engagements WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchall()
        eng_ages = [_days_between(er[0], now) for er in eng_rows]

        score = compute_score(tier, age_days, eng_ages, config.boost_fraction)

        updates.append((
            score,
            tier.name,
            tier.base_value,
            now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            chunk_id,
        ))

    if updates:
        conn.executemany(
            "UPDATE chunk_scores"
            " SET score = ?, tier = ?, base_value = ?, last_recomputed = ?"
            " WHERE chunk_id = ?",
            updates,
        )
        conn.commit()
        total = len(updates)

    log.info("Recomputed %d chunk scores", total)
    return total


def record_engagement(
    conn: sqlite3.Connection,
    chunk_id: int,
    event_type: str,
    source: str | None = None,
) -> None:
    """Record an explicit engagement event for a chunk."""
    conn.execute(
        "INSERT INTO engagements (chunk_id, event_type, source) VALUES (?, ?, ?)",
        (chunk_id, event_type, source),
    )
    conn.commit()
