-- Migration 0025 — Add web_page_id FK to search_actions and watch_actions
-- Created: 2026-05-23
--
-- WPEF inherited deliverable (SearchAction → WebPage FK retrofit). Every
-- SearchAction / WatchAction row that has a clicked-through URL gets a
-- web_page_id FK populated via upsert_web_page; junk searches leave it NULL.

ALTER TABLE search_actions ADD COLUMN web_page_id INTEGER REFERENCES web_pages(id);
ALTER TABLE watch_actions ADD COLUMN web_page_id INTEGER REFERENCES web_pages(id);

CREATE INDEX IF NOT EXISTS idx_search_actions_web_page_id ON search_actions(web_page_id);
CREATE INDEX IF NOT EXISTS idx_watch_actions_web_page_id ON watch_actions(web_page_id);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0025_google_activity_fk');
