-- Migration 004 — Bookmarks (Raindrop.io + scattered older bookmark backups)
-- Created: 2026-05-04 (revised 2026-05-05 — single-table model with instrument column)
--
-- DRAFT — pending Opus review per `2026-05-04g-cowork-session-handoff.md`.
-- Do not apply via init_db.py until reviewed.
--
-- Schema-blessed core: BookmarkAction (Schema.org subtype of OrganizeAction → Action).
-- "An agent bookmarks/flags/labels/registers/tags/marks an object."
--
-- Single-table model: one row per (normalized_url, instrument). The `instrument`
-- column is Schema.org Action.instrument — "the object that helped the agent
-- perform the action" — populated with the tool that did the bookmarking
-- ('raindrop', 'chrome-bookmarks', 'session-buddy', 'safari', 'toby', 'ie-favorites').
--
-- Default search filter is `WHERE instrument='raindrop'` — Raindrop is canonical
-- but everything is retained for query / manipulation.

-- ============================================================================
-- bookmarks — one row per (normalized_url × instrument)
-- ============================================================================
CREATE TABLE IF NOT EXISTS bookmarks (
    id                          INTEGER PRIMARY KEY,
    schema_type                 TEXT NOT NULL DEFAULT 'BookmarkAction',  -- Schema.org @type

    -- Identity
    instrument                  TEXT NOT NULL,        -- Schema.org Action.instrument: 'raindrop', 'chrome-bookmarks', 'session-buddy', 'safari', 'toby', 'ie-favorites'
    url                         TEXT NOT NULL,        -- as-saved URL, preserved verbatim
    normalized_url              TEXT NOT NULL,        -- url normalized for cross-instrument matching
    raindrop_id                 TEXT,                 -- only populated when instrument='raindrop'

    -- Schema-blessed content
    title                       TEXT,                 -- → schema:name
    note                        TEXT,                 -- → schema:description (user-authored note)

    -- Page-level properties (denormalized convenience per Path B; technically these
    -- live on the bookmarked WebPage object, not the BookmarkAction)
    excerpt                     TEXT,                 -- Raindrop-extracted page summary (page-level)
    cover_url                   TEXT,                 -- page-level image URL

    -- Raindrop-specific extensions (no clean Schema.org Action property)
    folder                      TEXT,                 -- Raindrop "Collection" or browser folder path
    tags                        TEXT,                 -- JSON array → schema:keywords
    favorite                    INTEGER NOT NULL DEFAULT 0,
    highlights                  TEXT,                 -- Raindrop-only; usually empty in current export

    -- Time (per-instrument tracking)
    first_seen_in_instrument    TEXT,                 -- earliest sighting for this (url, instrument)
    last_seen_in_instrument     TEXT,                 -- latest sighting for this (url, instrument)
    raindrop_created            TEXT,                 -- exact Raindrop-side created (only when instrument='raindrop')

    -- Counts
    appearance_count            INTEGER NOT NULL DEFAULT 1,

    -- Status
    excluded                    INTEGER NOT NULL DEFAULT 0,    -- 1 = junk-flagged
    excluded_reason             TEXT,                          -- e.g., 'junk:gmail-root'

    -- Provenance — points at the most recent source_files row for this row
    source_file_id              INTEGER REFERENCES source_files(id),
    raw_hash                    TEXT,

    ingested_at                 TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Identity: one row per (URL × instrument). Increment appearance_count on conflict.
CREATE UNIQUE INDEX IF NOT EXISTS idx_bookmarks_url_instrument
    ON bookmarks(normalized_url, instrument);

-- Default-filter index — almost every query will start with `WHERE instrument='raindrop'`.
CREATE INDEX IF NOT EXISTS idx_bookmarks_instrument         ON bookmarks(instrument);

-- Cross-instrument lookup (e.g., "everywhere this URL appears").
CREATE INDEX IF NOT EXISTS idx_bookmarks_normalized_url     ON bookmarks(normalized_url);
CREATE INDEX IF NOT EXISTS idx_bookmarks_folder             ON bookmarks(folder);
CREATE INDEX IF NOT EXISTS idx_bookmarks_first_seen         ON bookmarks(first_seen_in_instrument);
CREATE INDEX IF NOT EXISTS idx_bookmarks_excluded           ON bookmarks(excluded);

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('004_bookmarks');
