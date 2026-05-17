-- Migration 0011 — decay scoring infrastructure
-- Created: 2026-05-17
--
-- Adds two tables for the decay policy (retrieval-weight scoring):
--   chunk_scores  — per-chunk score, tier, and recompute metadata
--   engagements   — per-event engagement log (feeds leaky integrator)
--
-- The scoring formula is a leaky integrator:
--   score = max(floor, base * decay(age) + Σ boost * decay(time_since_engagement_i))
-- where decay = e^(-λt), λ = ln(2) / half_life_days
--
-- Tables are created empty. Initial population runs via Python
-- (src/phdb/scoring.py::populate_initial_scores) because the tier
-- assignment logic requires source_kind lookup + exponential math.
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS engagements;
--   DROP TABLE IF EXISTS chunk_scores;

-- ============================================================================
-- 1. chunk_scores — one row per chunk
-- ============================================================================
CREATE TABLE IF NOT EXISTS chunk_scores (
    chunk_id       INTEGER PRIMARY KEY,
    score          REAL    NOT NULL DEFAULT 1.0,
    tier           TEXT    NOT NULL DEFAULT 'standard',
    base_value     REAL    NOT NULL DEFAULT 1.0,
    tier_override  TEXT,
    last_recomputed TEXT   NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunk_scores_tier ON chunk_scores(tier);
CREATE INDEX IF NOT EXISTS idx_chunk_scores_score ON chunk_scores(score DESC);

-- ============================================================================
-- 2. engagements — row per explicit interaction event
-- ============================================================================
CREATE TABLE IF NOT EXISTS engagements (
    id         INTEGER PRIMARY KEY,
    chunk_id   INTEGER NOT NULL,
    timestamp  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    event_type TEXT    NOT NULL,
    source     TEXT,
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_engagements_chunk ON engagements(chunk_id);
CREATE INDEX IF NOT EXISTS idx_engagements_timestamp ON engagements(timestamp DESC);

-- ============================================================================
-- 3. Record this migration as applied
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0011_decay_scoring');
