-- Migration 0014 — commit authorship classification
-- Created: 2026-05-19
--
-- Annotation layer mapping (repo, commit_sha) → authorship_class so the
-- Skill Graph readiness engine can distinguish Rob-authored commits from
-- AI-co-authored ones.  Hard dependency of Skill Graph Phase 5.
--
-- Two tables:
--   commit_authorship       — per-commit classification
--   commit_authorship_repos — per-repo default class + metadata
--
-- Authorship classes (V1): rob-authored, ai-coauthored, external
--
-- Purely additive — no existing rows move or drop.
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS commit_authorship;
--   DROP TABLE IF EXISTS commit_authorship_repos;

-- ============================================================================
-- 1. commit_authorship_repos — per-repo defaults and metadata
-- ============================================================================
CREATE TABLE IF NOT EXISTS commit_authorship_repos (
    id                  INTEGER PRIMARY KEY,
    repo                TEXT    NOT NULL UNIQUE,  -- short name (vault, vault-mcp, personal-history-db)
    repo_path           TEXT,                     -- absolute path on disk
    default_class       TEXT    NOT NULL DEFAULT 'ai-coauthored',  -- fallback for commits without explicit row
    first_commit_date   TEXT,                     -- ISO 8601
    notes               TEXT,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ============================================================================
-- 2. commit_authorship — per-commit override / explicit classification
-- ============================================================================
CREATE TABLE IF NOT EXISTS commit_authorship (
    id                  INTEGER PRIMARY KEY,
    repo                TEXT    NOT NULL,          -- matches commit_authorship_repos.repo
    sha                 TEXT    NOT NULL,          -- full 40-char hex
    authorship_class    TEXT    NOT NULL,           -- rob-authored | ai-coauthored | external
    source              TEXT    NOT NULL DEFAULT 'trailer',  -- trailer | heuristic | manual
    commit_date         TEXT,                      -- ISO 8601 author date
    subject             TEXT,                      -- first line of commit message
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_commit_authorship_dedup
    ON commit_authorship(repo, sha);
CREATE INDEX IF NOT EXISTS idx_commit_authorship_class
    ON commit_authorship(repo, authorship_class);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0014_commit_authorship');
