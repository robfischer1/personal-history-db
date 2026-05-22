-- Migration 0023 — WebPage Entity Factoring
-- Created: 2026-05-22
--
-- Reshapes web_pages from a messages-decomposition typed table (30 Safari rows
-- with subject/sender_address/direction columns) into a URL-identity entity table.
-- Populates from bookmarks (one web_page per unique normalized_url).
-- Adds web_page_id FK to bookmarks and backfills.
--
-- Phase 0 decision #1 override: Safari rows preserved in _web_pages_safari_backup
-- for Python post-migration script to handle.

-- ============================================================================
-- Step 1: Backup existing web_pages (30 Safari rows)
-- ============================================================================
ALTER TABLE web_pages RENAME TO _web_pages_safari_backup;

-- ============================================================================
-- Step 2: Create new web_pages with URL-entity schema
-- ============================================================================
CREATE TABLE web_pages (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'WebPage',

    -- URL identity
    url             TEXT NOT NULL,           -- original URL (first seen)
    normalized_url  TEXT NOT NULL,           -- dedup key (one row per normalized URL)

    -- Page-level metadata (last-write-wins from any instrument)
    title           TEXT,
    excerpt         TEXT,
    cover_url       TEXT,
    domain          TEXT,                    -- derived from normalized_url at insert time

    -- Temporal envelope (across all instruments/actions)
    first_seen      TEXT,
    last_seen       TEXT,

    -- Provenance
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX idx_web_pages_normalized_url ON web_pages(normalized_url);
CREATE INDEX idx_web_pages_domain ON web_pages(domain);

-- ============================================================================
-- Step 3: Populate from bookmarks (GROUP BY normalized_url)
-- ============================================================================
INSERT INTO web_pages (url, normalized_url, title, excerpt, cover_url, domain,
                       first_seen, last_seen, source_file_id)
SELECT
    -- Pick one representative raw URL per normalized group
    url,
    normalized_url,
    -- Page-level fields: pick best non-null value across instruments
    MAX(title),
    MAX(excerpt),
    MAX(cover_url),
    -- Domain: extract netloc from normalized URL
    -- All normalized URLs have scheme://netloc/... form
    CASE
        WHEN INSTR(normalized_url, '://') > 0 THEN
            SUBSTR(normalized_url, INSTR(normalized_url, '://') + 3,
                CASE WHEN INSTR(SUBSTR(normalized_url, INSTR(normalized_url, '://') + 3), '/') > 0
                THEN INSTR(SUBSTR(normalized_url, INSTR(normalized_url, '://') + 3), '/') - 1
                ELSE LENGTH(SUBSTR(normalized_url, INSTR(normalized_url, '://') + 3))
                END)
        ELSE NULL
    END,
    MIN(first_seen_in_instrument),
    MAX(last_seen_in_instrument),
    MAX(source_file_id)
FROM bookmarks
GROUP BY normalized_url;

-- ============================================================================
-- Step 4: Add web_page_id FK to bookmarks
-- ============================================================================
ALTER TABLE bookmarks ADD COLUMN web_page_id INTEGER REFERENCES web_pages(id);

-- ============================================================================
-- Step 5: Backfill FK
-- ============================================================================
UPDATE bookmarks SET web_page_id = (
    SELECT wp.id FROM web_pages wp
    WHERE wp.normalized_url = bookmarks.normalized_url
);

CREATE INDEX idx_bookmarks_web_page_id ON bookmarks(web_page_id);

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0023_web_pages_entity');
