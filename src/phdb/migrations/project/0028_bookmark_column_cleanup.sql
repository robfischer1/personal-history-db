-- Migration 0028 — WPEF follow-on: drop deprecated bookmark columns
-- Created: 2026-05-23
--
-- Drops the columns from `bookmarks` that became redundant after the
-- WebPage entity factoring (migration 0023). These columns now live
-- on the parent `web_pages` entity, joinable via `web_page_id`:
--
--   url, normalized_url, title, excerpt, cover_url
--
-- Also replaces the unique index keyed on `(normalized_url, instrument)`
-- with `(web_page_id, instrument)` — bookmark dedup is now keyed on the
-- entity FK rather than the duplicated URL column.
--
-- SQLite doesn't support DROP COLUMN for indexed columns cleanly, so we
-- use the table-rebuild pattern (CREATE new / INSERT...SELECT / DROP old
-- / RENAME).

-- ============================================================================
-- Step 1: Create the slim bookmarks_new table
-- ============================================================================
CREATE TABLE bookmarks_new (
    id                          INTEGER PRIMARY KEY,
    schema_type                 TEXT NOT NULL DEFAULT 'BookmarkAction',

    -- Identity (instrument-scoped; URL identity lives on web_pages)
    instrument                  TEXT NOT NULL,
    raindrop_id                 TEXT,

    -- Schema-blessed content (user-authored)
    note                        TEXT,

    -- Raindrop-specific extensions
    folder                      TEXT,
    tags                        TEXT,
    favorite                    INTEGER NOT NULL DEFAULT 0,
    highlights                  TEXT,

    -- Time (per-instrument tracking)
    first_seen_in_instrument    TEXT,
    last_seen_in_instrument     TEXT,
    raindrop_created            TEXT,

    -- Counts
    appearance_count            INTEGER NOT NULL DEFAULT 1,

    -- Status
    excluded                    INTEGER NOT NULL DEFAULT 0,
    excluded_reason             TEXT,

    -- Provenance
    source_file_id              INTEGER REFERENCES source_files(id),
    raw_hash                    TEXT,

    ingested_at                 TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    -- Entity FK (post-WPEF, now the dedup key)
    web_page_id                 INTEGER REFERENCES web_pages(id)
);

-- ============================================================================
-- Step 2: Copy the surviving columns from the old table
-- ============================================================================
INSERT INTO bookmarks_new (
    id, schema_type, instrument, raindrop_id, note, folder, tags, favorite,
    highlights, first_seen_in_instrument, last_seen_in_instrument,
    raindrop_created, appearance_count, excluded, excluded_reason,
    source_file_id, raw_hash, ingested_at, web_page_id
)
SELECT
    id, schema_type, instrument, raindrop_id, note, folder, tags, favorite,
    highlights, first_seen_in_instrument, last_seen_in_instrument,
    raindrop_created, appearance_count, excluded, excluded_reason,
    source_file_id, raw_hash, ingested_at, web_page_id
FROM bookmarks;

-- ============================================================================
-- Step 3: Drop the old table + its indexes (DROP TABLE cleans up indexes)
-- ============================================================================
DROP TABLE bookmarks;

-- ============================================================================
-- Step 4: Rename + recreate the indexes on the new table
-- ============================================================================
ALTER TABLE bookmarks_new RENAME TO bookmarks;

-- New unique dedup key — bookmark-per-(web_page × instrument).
-- Replaces the dropped idx_bookmarks_url_instrument which keyed on
-- (normalized_url, instrument).
CREATE UNIQUE INDEX idx_bookmarks_web_page_instrument
    ON bookmarks(web_page_id, instrument);

-- Default-filter index — almost every query starts with
-- `WHERE instrument='raindrop'`.
CREATE INDEX idx_bookmarks_instrument         ON bookmarks(instrument);
CREATE INDEX idx_bookmarks_folder             ON bookmarks(folder);
CREATE INDEX idx_bookmarks_first_seen         ON bookmarks(first_seen_in_instrument);
CREATE INDEX idx_bookmarks_excluded           ON bookmarks(excluded);
CREATE INDEX idx_bookmarks_web_page_id        ON bookmarks(web_page_id);

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0028_bookmark_column_cleanup');
