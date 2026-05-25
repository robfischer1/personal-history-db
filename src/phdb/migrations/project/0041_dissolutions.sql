-- Migration 0041 — dissolutions + materialization_events + link table + views
-- Created: 2026-05-24
--
-- Vault-DB lifecycle event registry per Outputs/Plans/Dissolution Tracking.md.
-- Layers on top of file_revisions (migration 0039) — classifies which
-- `delete` rows belong to dissolution waves and which DB tables / Schema.org
-- @types now own that content. The sister table materialization_events tracks
-- the reverse direction (DB-canonical content surfaced back as vault stubs
-- via articles_materialize.py / tasks_materialize.py).
--
-- Phase 0 overrides (locked 2026-05-24, Dissolution Tracking DECISIONS.md):
--   Q3  migration_id is nullable (Articles pilot may dissolve without one)
--   Q8  verbose CLI (`waves`, `status`, `audit` subverbs)
--   Q13 max-coupling — registry is a full vault-DB lifecycle store, not just
--       a dissolution log; materialization_events sister table added
--   Q14 multi-repo from day 1 — repo column generalized; defaults to 'vault'
--
-- ROLLBACK:
--   DROP VIEW IF EXISTS v_vault_path_lifecycle;
--   DROP VIEW IF EXISTS v_file_revisions_classified;
--   DROP TABLE IF EXISTS materialization_events;
--   DROP TABLE IF EXISTS file_revision_dissolutions;
--   DROP TABLE IF EXISTS dissolutions;

-- ============================================================================
-- 1. dissolutions — wave-level dissolution events
-- ============================================================================
CREATE TABLE IF NOT EXISTS dissolutions (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'DissolutionEvent',
    repo            TEXT NOT NULL DEFAULT 'vault',
    plan_pk         INTEGER REFERENCES plans(id),
    plan_slug       TEXT NOT NULL,                  -- denormalized; resilient to plan_pk churn
    migration_id    TEXT,                           -- nullable per Q3
    commit_sha      TEXT,                           -- nullable when wave spans multiple commits
    target_schemas  TEXT NOT NULL,                  -- JSON array of Schema.org @types
    target_tables   TEXT NOT NULL,                  -- JSON array of phdb table names
    rationale       TEXT,                           -- required when migration_id is NULL (Q11)
    dissolved_at    TEXT NOT NULL,                  -- ISO 8601
    declared_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    declared_by     TEXT NOT NULL                   -- 'cowork' / 'code' / 'backfill'
);

-- SQLite UNIQUE constraint treats NULLs as distinct, so multiple
-- (plan_pk, NULL) rows are allowed; that's intentional per Q10.
CREATE UNIQUE INDEX IF NOT EXISTS idx_dissolutions_dedup
    ON dissolutions(plan_pk, migration_id);
CREATE INDEX IF NOT EXISTS idx_dissolutions_plan_slug
    ON dissolutions(plan_slug);
CREATE INDEX IF NOT EXISTS idx_dissolutions_dissolved_at
    ON dissolutions(dissolved_at);
CREATE INDEX IF NOT EXISTS idx_dissolutions_repo_dissolved
    ON dissolutions(repo, dissolved_at);

-- ============================================================================
-- 2. file_revision_dissolutions — link table
-- ============================================================================
CREATE TABLE IF NOT EXISTS file_revision_dissolutions (
    id                INTEGER PRIMARY KEY,
    file_revision_pk  INTEGER NOT NULL REFERENCES file_revisions(id) ON DELETE CASCADE,
    dissolution_pk    INTEGER NOT NULL REFERENCES dissolutions(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_file_revision_dissolutions_dedup
    ON file_revision_dissolutions(file_revision_pk, dissolution_pk);
CREATE INDEX IF NOT EXISTS idx_file_revision_dissolutions_dissolution
    ON file_revision_dissolutions(dissolution_pk);

-- ============================================================================
-- 3. materialization_events — per-file materialization events (Q13)
-- ============================================================================
CREATE TABLE IF NOT EXISTS materialization_events (
    id                      INTEGER PRIMARY KEY,
    schema_type             TEXT NOT NULL DEFAULT 'MaterializationEvent',
    repo                    TEXT NOT NULL DEFAULT 'vault',
    file_path               TEXT NOT NULL,                  -- vault-relative POSIX
    source_dissolution_pk   INTEGER REFERENCES dissolutions(id),
    source_table            TEXT NOT NULL,                  -- phdb table the content was materialized from
    source_row_id           INTEGER,                        -- nullable for aggregate materializations (TODO.md)
    materializer            TEXT NOT NULL,                  -- 'articles_materialize' / 'tasks_materialize' / etc.
    materialized_at         TEXT NOT NULL,
    materialization_kind    TEXT NOT NULL DEFAULT 'stub'    -- 'stub' / 'aggregate' / 'full'
);

CREATE INDEX IF NOT EXISTS idx_materialization_events_path_ts
    ON materialization_events(file_path, materialized_at);
CREATE INDEX IF NOT EXISTS idx_materialization_events_source
    ON materialization_events(source_dissolution_pk);

-- ============================================================================
-- 4. v_file_revisions_classified — dissolution-aware view over file_revisions
-- ============================================================================
DROP VIEW IF EXISTS v_file_revisions_classified;
CREATE VIEW v_file_revisions_classified AS
SELECT
    fr.id                AS file_revision_pk,
    fr.repo              AS repo,
    fr.commit_sha        AS commit_sha,
    fr.file_path         AS file_path,
    fr.change_type       AS change_type,
    fr.authorship        AS authorship,
    fr.captured_at       AS captured_at,
    frd.dissolution_pk   AS dissolution_pk,
    d.plan_slug          AS dissolution_plan_slug,
    d.migration_id       AS dissolution_migration_id,
    d.target_schemas     AS dissolution_target_schemas,
    d.target_tables      AS dissolution_target_tables,
    d.dissolved_at       AS dissolution_dissolved_at,
    CASE WHEN frd.dissolution_pk IS NOT NULL THEN 1 ELSE 0 END AS is_dissolution
FROM file_revisions fr
LEFT JOIN file_revision_dissolutions frd ON frd.file_revision_pk = fr.id
LEFT JOIN dissolutions d ON d.id = frd.dissolution_pk;

-- ============================================================================
-- 5. v_vault_path_lifecycle — full lifecycle for any vault path
-- ============================================================================
-- Returns dissolution + materialization events for a path, ordered
-- chronologically by event timestamp. Use to answer "what happened to
-- this file?" — dissolved, materialized, possibly re-dissolved.
DROP VIEW IF EXISTS v_vault_path_lifecycle;
CREATE VIEW v_vault_path_lifecycle AS
SELECT
    fr.repo            AS repo,
    fr.file_path       AS file_path,
    'dissolution'      AS event_type,
    d.id               AS event_pk,
    d.plan_slug        AS plan_slug_or_materializer,
    d.target_tables    AS source_or_target,
    d.dissolved_at     AS event_at,
    NULL               AS materialization_kind
FROM dissolutions d
JOIN file_revision_dissolutions frd ON frd.dissolution_pk = d.id
JOIN file_revisions fr ON fr.id = frd.file_revision_pk
UNION ALL
SELECT
    me.repo            AS repo,
    me.file_path       AS file_path,
    'materialization'  AS event_type,
    me.id              AS event_pk,
    me.materializer    AS plan_slug_or_materializer,
    me.source_table    AS source_or_target,
    me.materialized_at AS event_at,
    me.materialization_kind AS materialization_kind
FROM materialization_events me
ORDER BY repo, file_path, event_at;

-- ============================================================================
-- 6. Record migration
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0041_dissolutions');
