-- Migration 0042 — vault_notes current-state note index
-- Created: 2026-05-27
--
-- Phase 8 of the Git for Ideas plan (Outputs/Plans/Git for Ideas.md).
-- One row per vault markdown file that has ever existed, holding the
-- latest body text, frontmatter metadata, and lifecycle status. Remote
-- MCP clients (Casper on llm01) can't `git cat-file` or read the vault
-- filesystem; this table gives them full-text + semantic search over
-- the vault's current and historical note state.
--
-- Dissolved and deleted notes retain their last-known body so they
-- remain searchable. Status is derived: `dissolved` when a matching
-- dissolutions registry row exists (migration 0041), `deleted` for
-- git-deleted files without a dissolution, `live` otherwise.
--
-- Embeddings use the existing chunks + doc_vectors infrastructure
-- (source_table='vault_notes') rather than a separate vec table.
--
-- ROLLBACK:
--   DROP TRIGGER IF EXISTS vault_notes_fts_ai;
--   DROP TRIGGER IF EXISTS vault_notes_fts_ad;
--   DROP TRIGGER IF EXISTS vault_notes_fts_au;
--   DROP TABLE IF EXISTS vault_notes_fts;
--   DROP TABLE IF EXISTS vault_notes;

-- ============================================================================
-- 1. vault_notes — current-state note index
-- ============================================================================
CREATE TABLE IF NOT EXISTS vault_notes (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'VaultNote',
    file_path         TEXT NOT NULL UNIQUE,        -- vault-relative POSIX path
    name              TEXT NOT NULL,                -- frontmatter `name:` or filename stem
    description       TEXT,                         -- frontmatter `description:` or file_revisions.summary fallback
    at_type           TEXT,                         -- frontmatter `@type`
    status            TEXT NOT NULL DEFAULT 'live'
                      CHECK (status IN ('live', 'dissolved', 'deleted')),
    body              TEXT,                         -- full markdown content (last-known state)
    latest_blob_sha   TEXT,                         -- git blob SHA for the stored body
    latest_commit_sha TEXT NOT NULL,                -- last commit that touched this file
    first_seen_commit TEXT NOT NULL,                -- commit where change_type='add'
    authorship        TEXT,                         -- latest file_revisions.authorship value
    captured_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_vault_notes_status
    ON vault_notes(status);
CREATE INDEX IF NOT EXISTS idx_vault_notes_at_type
    ON vault_notes(at_type);
CREATE INDEX IF NOT EXISTS idx_vault_notes_name
    ON vault_notes(name);

-- ============================================================================
-- 2. vault_notes_fts — full-text search over name + description + body
-- ============================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS vault_notes_fts USING fts5(
    name,
    description,
    body,
    content='vault_notes',
    content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);

-- Keep FTS index in sync via triggers (matches chunks/doc_fts pattern).
CREATE TRIGGER IF NOT EXISTS vault_notes_fts_ai
    AFTER INSERT ON vault_notes
BEGIN
    INSERT INTO vault_notes_fts(rowid, name, description, body)
    VALUES (new.id, new.name, new.description, new.body);
END;

CREATE TRIGGER IF NOT EXISTS vault_notes_fts_ad
    AFTER DELETE ON vault_notes
BEGIN
    INSERT INTO vault_notes_fts(vault_notes_fts, rowid, name, description, body)
    VALUES ('delete', old.id, old.name, old.description, old.body);
END;

CREATE TRIGGER IF NOT EXISTS vault_notes_fts_au
    AFTER UPDATE OF name, description, body ON vault_notes
BEGIN
    INSERT INTO vault_notes_fts(vault_notes_fts, rowid, name, description, body)
    VALUES ('delete', old.id, old.name, old.description, old.body);
    INSERT INTO vault_notes_fts(rowid, name, description, body)
    VALUES (new.id, new.name, new.description, new.body);
END;

-- ============================================================================
-- 3. Record migration
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0042_vault_notes');
