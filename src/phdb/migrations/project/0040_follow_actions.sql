-- Migration: 0040_follow_actions.sql
-- Created: 2026-05-24
-- Description: Add follow_actions canonical table for FollowAction emissions
--              (YouTube subscriptions; future Twitch/X/Bluesky follows).
--
-- Greenfield table — no backfill from messages. Shape mirrors WatchAction
-- (messages-decomp + platform_name + web_page_id FK to web_pages), with
-- channel_name as the FollowAction-specific extra and date_followed as the
-- date column. The first emitter is the refactored youtube_activity plugin
-- (replacing the dropped 0037_youtube_activity migration).

CREATE TABLE IF NOT EXISTS follow_actions (
    id                 INTEGER PRIMARY KEY,
    schema_type        TEXT NOT NULL DEFAULT 'FollowAction',
    follow_key         TEXT,
    subject            TEXT,
    platform_name      TEXT,
    channel_name       TEXT,
    web_page_id        INTEGER REFERENCES web_pages(id),
    direction          TEXT NOT NULL DEFAULT 'self',
    date_followed      TEXT,
    body_text          TEXT,
    body_text_source   TEXT,
    body_text_hash     TEXT,
    is_bulk            INTEGER NOT NULL DEFAULT 1,
    bulk_signal        TEXT,
    source_byte_offset INTEGER,
    source_byte_length INTEGER,
    raw_hash           TEXT,
    source_file_id     INTEGER REFERENCES source_files(id),
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_follow_actions_dedup ON follow_actions(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_follow_actions_date ON follow_actions(date_followed);
CREATE INDEX IF NOT EXISTS idx_follow_actions_web_page_id ON follow_actions(web_page_id);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0040_follow_actions');
