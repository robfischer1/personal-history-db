-- Migration 0039 — file_revisions index over git history
-- Created: 2026-05-24
--
-- "Git for Ideas" intelligence layer (Phase 0 locked 2026-05-24,
-- Outputs/Plans/Git for Ideas.md + Outputs/Plans/Git for Ideas DECISIONS.md).
--
-- One row per (repo, commit_sha, file_path) where a markdown file changed.
-- Bodies are NOT stored — git already holds them; rows reference
-- git_blob_sha and parent_blob_sha so callers materialize via
-- `git cat-file -p <sha>` against the repo at
-- commit_authorship_repos.repo_path.
--
-- The DB is an intelligence layer over git:
--   - summary       — AI-generated "what changed and why" prose (Phase 4)
--   - triple deltas — predicate-graph edges added/removed per revision (Phase 5)
--   - change_type   — git-native (add/modify/delete/rename)
--   - authorship    — rob | ai (derived from commit_authorship.authorship_class)
--
-- Parent FK: commit_authorship(repo, sha). Not declared as a hard SQL FK
-- because commit_authorship's (repo, sha) is unique-indexed but not the
-- primary key, and we want walker re-runs to be tolerant of authorship
-- catch-up timing. The walker is responsible for ensuring the
-- commit_authorship row exists before inserting a file_revisions row.
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS revision_triple_deltas;
--   DROP TABLE IF EXISTS file_revisions;

-- ============================================================================
-- 1. file_revisions — per-commit per-file revision index
-- ============================================================================
CREATE TABLE IF NOT EXISTS file_revisions (
    id                    INTEGER PRIMARY KEY,
    schema_type           TEXT NOT NULL DEFAULT 'FileRevision',
    repo                  TEXT NOT NULL,            -- matches commit_authorship.repo
    commit_sha            TEXT NOT NULL,            -- 40-char hex
    file_path             TEXT NOT NULL,            -- vault-relative POSIX path (forward slashes)
    git_blob_sha          TEXT NOT NULL,            -- materialize via `git cat-file -p`
    parent_blob_sha       TEXT,                     -- prior blob; NULL for `add`
    change_type           TEXT NOT NULL CHECK (change_type IN ('add', 'modify', 'delete', 'rename')),
    authorship            TEXT NOT NULL CHECK (authorship IN ('rob', 'ai')),
    prior_file_path       TEXT,                     -- populated for `rename`
    summary               TEXT,                     -- AI-generated, NULL until Phase 4 worker runs
    summary_model         TEXT,
    summary_generated_at  TEXT,
    captured_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_file_revisions_dedup
    ON file_revisions(repo, commit_sha, file_path);
CREATE INDEX IF NOT EXISTS idx_file_revisions_history
    ON file_revisions(file_path, commit_sha);
CREATE INDEX IF NOT EXISTS idx_file_revisions_authorship
    ON file_revisions(authorship, captured_at);
-- Partial index — drives the Phase 4 async summary worker queue.
CREATE INDEX IF NOT EXISTS idx_file_revisions_unsumm
    ON file_revisions(captured_at) WHERE summary IS NULL;

-- ============================================================================
-- 2. revision_triple_deltas — predicate-graph edges per revision
-- ============================================================================
CREATE TABLE IF NOT EXISTS revision_triple_deltas (
    id              INTEGER PRIMARY KEY,
    revision_pk     INTEGER NOT NULL REFERENCES file_revisions(id) ON DELETE CASCADE,
    op              TEXT NOT NULL CHECK (op IN ('add', 'remove')),
    subject_node_pk INTEGER,
    predicate_pk    INTEGER,
    object_node_pk  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_revision_triple_deltas_rev
    ON revision_triple_deltas(revision_pk, op);
CREATE INDEX IF NOT EXISTS idx_revision_triple_deltas_pred
    ON revision_triple_deltas(predicate_pk);

-- ============================================================================
-- 3. Record this migration as applied
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0039_file_revisions');
