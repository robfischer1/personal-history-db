-- Migration: 0027_read_actions.sql
-- Created: 2026-05-23
-- Description: Add read_actions table for Pocket/Instapaper reading-list events.
--
-- WPEF inherited follow-on brief 102. Third consumer of the entity-FK pattern
-- after BookmarkAction (bookmarks) and BrowseAction (browse_actions): reading
-- list entries are events that FK into the WebPage entity. Schema is ready;
-- the matching phdb.plugins.readaction stub plugin awaits a Pocket /
-- Instapaper format parser.

CREATE TABLE IF NOT EXISTS read_actions (
    id INTEGER PRIMARY KEY,
    schema_type TEXT NOT NULL DEFAULT 'ReadAction',
    web_page_id INTEGER REFERENCES web_pages(id),
    date_read TEXT,
    direction TEXT NOT NULL DEFAULT 'self',
    body_text TEXT,
    body_text_source TEXT,
    source_file_id INTEGER REFERENCES source_files(id),
    raw_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_read_actions_dedup ON read_actions(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_read_actions_web_page_id ON read_actions(web_page_id);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0027_read_actions');
