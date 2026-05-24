-- Migration 0036: search_history
--
-- Google Takeout Search MyActivity HTML export schema.
-- One table:
--   search_history — one row per search query or visited result
--
-- Design notes:
--   - query is the search text (from <a> tag text content)
--   - url is the google.com/search?q= URL
--   - clicked_url captures "Visited" entries (clicked search results)
--   - timestamp stores Unix epoch seconds
--   - location_lat/location_lon from Maps center= parameter
--   - dedup on (query, timestamp) — same query at the same second is a duplicate

CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY,
    query TEXT NOT NULL,
    url TEXT,                           -- the google.com/search?q= URL
    clicked_url TEXT,                   -- if this was a "Visited" entry
    timestamp INTEGER NOT NULL,         -- Unix epoch seconds
    source TEXT NOT NULL DEFAULT 'google',
    location_lat REAL,
    location_lon REAL,
    product TEXT,                       -- "Search", etc.
    source_file TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_search_history_query ON search_history(query);
CREATE INDEX IF NOT EXISTS idx_search_history_timestamp ON search_history(timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_search_history_dedup ON search_history(query, timestamp);

INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0036_search_history');
