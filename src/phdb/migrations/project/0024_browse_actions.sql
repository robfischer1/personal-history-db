-- Migration: 0024_browse_actions.sql
-- Created: 2026-05-22
-- Description: Add browse_actions table for Safari/Chrome history tracking (post-WPEF).

CREATE TABLE IF NOT EXISTS browse_actions (
    id INTEGER PRIMARY KEY,
    schema_type TEXT NOT NULL DEFAULT 'BrowseAction',
    web_page_id INTEGER REFERENCES web_pages(id),
    visit_time TEXT,
    source_device TEXT,
    raw_hash TEXT,
    source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_browse_actions_web_page_id ON browse_actions(web_page_id);
CREATE INDEX IF NOT EXISTS idx_browse_actions_visit_time ON browse_actions(visit_time);
CREATE UNIQUE INDEX IF NOT EXISTS idx_browse_actions_dedup ON browse_actions(source_file_id, raw_hash);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0024_browse_actions');
