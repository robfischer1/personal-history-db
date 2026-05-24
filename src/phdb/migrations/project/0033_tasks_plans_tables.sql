-- Migration 0033 — Create tasks + plans tables, add predicates
-- Created: 2026-05-23
--
-- Tasks and Projects Dissolution (Outputs/Plans/Tasks and Projects Dissolution.md).
-- Two tables: `tasks` (one row per vault task) and `plans` (one row per vault plan).
-- Tasks are identity-bearing (updated in place on status transitions).
-- Plans are metadata-only — body prose stays on disk as markdown.
--
-- Also adds 5 predicates for task/plan relationships:
-- belongsTo (task→plan), blockedBy, dependsOn, supersedes,
-- closedDuring (task→session).
--
-- Rollback: DROP TABLE tasks; DROP TABLE plans;
--           DELETE FROM predicates WHERE id BETWEEN 43 AND 52;

-- ---- tasks ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'Action',
    name            TEXT NOT NULL,
    identifier      TEXT,
    tier            TEXT,                -- opus, sonnet, haiku, backlog
    status          TEXT NOT NULL,       -- open, in-progress, complete, deferred, superseded, discarded
    effort          TEXT,                -- XS, S, M, L, XL
    maintenance     TEXT,                -- none, light, heavy
    project         TEXT,                -- wikilink to parent project
    created         TEXT,                -- ISO 8601 date from frontmatter
    updated         TEXT,                -- ISO 8601 date from frontmatter
    closure_date    TEXT,                -- ISO 8601 date (when status=complete)
    closure_evidence TEXT,               -- narrative summary
    file_path       TEXT,                -- vault-relative path
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_dedup ON tasks(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_tasks_name ON tasks(name);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_tier ON tasks(tier);

-- ---- plans ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plans (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'Plan',
    name            TEXT NOT NULL,
    identifier      TEXT,
    description     TEXT,
    status          TEXT NOT NULL,       -- draft, active, complete, deferred
    phase           TEXT,                -- integer string or "complete"
    effort          TEXT,                -- XS, S, M, L, XL
    maintenance     TEXT,                -- none, light, heavy
    created         TEXT,                -- ISO 8601 date from frontmatter
    updated         TEXT,                -- ISO 8601 date from frontmatter
    file_path       TEXT,                -- vault-relative path (body stays on disk)
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_dedup ON plans(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_plans_name ON plans(name);
CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);

-- ---- Predicates for task/plan relationships ---------------------------------

INSERT OR IGNORE INTO predicates (id, name, symmetric, description) VALUES
    (43, 'belongsTo',     0, 'Task belongs to a plan (task→plan)'),
    (44, 'hasTask',       0, 'Plan has a task (plan→task, inverse of belongsTo)'),
    (45, 'blockedBy',     0, 'Task or plan is blocked by another task or plan'),
    (46, 'blocks',        0, 'Task or plan blocks another (inverse of blockedBy)'),
    (47, 'dependsOn',     0, 'Task or plan depends on another task or plan'),
    (48, 'dependedOnBy',  0, 'Inverse of dependsOn'),
    (49, 'supersedes',    0, 'Task or plan supersedes another'),
    (50, 'supersededBy',  0, 'Inverse of supersedes'),
    (51, 'closedDuring',  0, 'Task was closed during a session (task→session)'),
    (52, 'closedTask',    0, 'Session closed a task (inverse of closedDuring)');

-- Wire inverse pairs
UPDATE predicates SET inverse_predicate_id = 44 WHERE id = 43;
UPDATE predicates SET inverse_predicate_id = 43 WHERE id = 44;
UPDATE predicates SET inverse_predicate_id = 46 WHERE id = 45;
UPDATE predicates SET inverse_predicate_id = 45 WHERE id = 46;
UPDATE predicates SET inverse_predicate_id = 48 WHERE id = 47;
UPDATE predicates SET inverse_predicate_id = 47 WHERE id = 48;
UPDATE predicates SET inverse_predicate_id = 50 WHERE id = 49;
UPDATE predicates SET inverse_predicate_id = 49 WHERE id = 50;
UPDATE predicates SET inverse_predicate_id = 52 WHERE id = 51;
UPDATE predicates SET inverse_predicate_id = 51 WHERE id = 52;

-- ---- Registration ------------------------------------------------------------
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0033_tasks_plans_tables');
