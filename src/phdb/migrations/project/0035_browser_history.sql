-- Migration: 0035_browser_history.sql
-- Created: 2026-05-23
-- Description: Add browser_history table for Chrome/browser history Takeout exports.
--
-- Chrome History.json from Google Takeout contains page visits with
-- microsecond timestamps, transition qualifiers, and client profile IDs.
-- The table stores Unix epoch seconds (converted from time_usec at ingest).
-- Dedup key: (url, timestamp, browser) — same URL at the same second from
-- the same browser is a duplicate.

CREATE TABLE IF NOT EXISTS browser_history (
    id                  INTEGER PRIMARY KEY,
    schema_type         TEXT    NOT NULL DEFAULT 'BrowserHistory',
    url                 TEXT    NOT NULL,
    title               TEXT,
    timestamp           INTEGER NOT NULL,          -- Unix epoch seconds (converted from time_usec)
    visit_duration_ms   INTEGER,
    page_transition     TEXT,                      -- page_transition_qualifier
    browser             TEXT    NOT NULL DEFAULT 'chrome',
    profile             TEXT,                      -- client_id
    source_file         TEXT,
    source_file_id      INTEGER REFERENCES source_files(id),
    raw_hash            TEXT,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_browser_history_url ON browser_history(url);
CREATE INDEX IF NOT EXISTS idx_browser_history_timestamp ON browser_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_browser_history_browser ON browser_history(browser);
CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_history_dedup ON browser_history(url, timestamp, browser);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0035_browser_history');
