-- Migration 0030 — Create 7 consumed-media entity tables, drop legacy books
-- Created: 2026-05-23
--
-- Consumed Media Dissolution (Outputs/Plans/Consumed Media Dissolution.md).
-- Dissolves 7 Entities/ subdirectories into DB-canonical typed tables with
-- Schema.org entity schemas. Column names sourced from Schema.org properties
-- (e.g. `author` not `creator`, `image` not `image_url`).
--
-- The old `books` table (migration 0008, messages-decomposition era) contains
-- 249 Goodreads title-only rows with no dates, body_text=title, and 50
-- orphaned chunks. Dropped and replaced with the new Schema.org-aligned table.
--
-- Rollback: DROP TABLE books; DROP TABLE games; DROP TABLE movies;
--           DROP TABLE tv_series; DROP TABLE podcasts;
--           DROP TABLE youtube_channels; DROP TABLE twitch_channels;
--           (then re-run 0008 to restore the legacy books table)

-- ---- Drop legacy books table ------------------------------------------------
-- 249 Goodreads title-only rows (messages-decomposition shape). 50 chunks
-- reference it; acceptable loss (title-only, no semantic value).
DROP TABLE IF EXISTS books;

-- ---- Shared column layout ---------------------------------------------------
-- All 7 tables share: id, schema_type, name, description, url, image,
-- identifier, alternate_name, author, publisher, date_published, genre,
-- keywords, file_path, source_file_id, raw_hash, created_at.
-- Type-specific columns follow per table.

-- ---- books (Book) -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS books (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'Book',
    -- Schema.org Thing
    name            TEXT NOT NULL,
    description     TEXT,
    url             TEXT,
    image           TEXT,
    identifier      TEXT,
    alternate_name  TEXT,           -- JSON array
    -- Schema.org CreativeWork
    author          TEXT,
    publisher       TEXT,
    date_published  TEXT,
    genre           TEXT,           -- JSON array of BISAC codes
    keywords        TEXT,           -- JSON array
    -- Book-specific
    isbn            TEXT,
    number_of_pages INTEGER,
    -- Operational
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_books_dedup ON books(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_books_name ON books(name);

-- ---- games (VideoGame) ------------------------------------------------------
CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'VideoGame',
    name            TEXT NOT NULL,
    description     TEXT,
    url             TEXT,
    image           TEXT,
    identifier      TEXT,
    alternate_name  TEXT,
    author          TEXT,
    publisher       TEXT,
    date_published  TEXT,
    genre           TEXT,
    keywords        TEXT,
    -- VideoGame-specific
    game_platform   TEXT,           -- JSON array
    -- Operational
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_games_dedup ON games(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_games_name ON games(name);

-- ---- movies (Movie) ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS movies (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'Movie',
    name            TEXT NOT NULL,
    description     TEXT,
    url             TEXT,
    image           TEXT,
    identifier      TEXT,
    alternate_name  TEXT,
    author          TEXT,
    publisher       TEXT,
    date_published  TEXT,
    genre           TEXT,
    keywords        TEXT,
    -- Movie-specific
    duration        TEXT,
    actor           TEXT,           -- JSON array
    director        TEXT,
    -- Operational
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_movies_dedup ON movies(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_movies_name ON movies(name);

-- ---- tv_series (TVSeries) ---------------------------------------------------
CREATE TABLE IF NOT EXISTS tv_series (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'TVSeries',
    name            TEXT NOT NULL,
    description     TEXT,
    url             TEXT,
    image           TEXT,
    identifier      TEXT,
    alternate_name  TEXT,
    author          TEXT,
    publisher       TEXT,
    date_published  TEXT,
    genre           TEXT,
    keywords        TEXT,
    -- TVSeries-specific
    start_date      TEXT,
    actor           TEXT,           -- JSON array
    number_of_seasons INTEGER,
    -- Operational
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tv_series_dedup ON tv_series(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_tv_series_name ON tv_series(name);

-- ---- podcasts (PodcastSeries) -----------------------------------------------
CREATE TABLE IF NOT EXISTS podcasts (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'PodcastSeries',
    name            TEXT NOT NULL,
    description     TEXT,
    url             TEXT,
    image           TEXT,
    identifier      TEXT,
    alternate_name  TEXT,
    author          TEXT,
    publisher       TEXT,
    date_published  TEXT,
    genre           TEXT,
    keywords        TEXT,
    -- PodcastSeries-specific
    start_date      TEXT,
    -- Operational
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_podcasts_dedup ON podcasts(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_podcasts_name ON podcasts(name);

-- ---- youtube_channels (WebSite) ---------------------------------------------
CREATE TABLE IF NOT EXISTS youtube_channels (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'WebSite',
    name            TEXT NOT NULL,
    description     TEXT,
    url             TEXT,
    image           TEXT,
    identifier      TEXT,
    alternate_name  TEXT,
    author          TEXT,
    publisher       TEXT,
    date_published  TEXT,
    genre           TEXT,
    keywords        TEXT,
    -- Operational
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_youtube_channels_dedup ON youtube_channels(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_youtube_channels_name ON youtube_channels(name);

-- ---- twitch_channels (WebSite) ----------------------------------------------
CREATE TABLE IF NOT EXISTS twitch_channels (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'WebSite',
    name            TEXT NOT NULL,
    description     TEXT,
    url             TEXT,
    image           TEXT,
    identifier      TEXT,
    alternate_name  TEXT,
    author          TEXT,
    publisher       TEXT,
    date_published  TEXT,
    genre           TEXT,
    keywords        TEXT,
    -- Operational
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_twitch_channels_dedup ON twitch_channels(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_twitch_channels_name ON twitch_channels(name);

-- ---- Register migration -----------------------------------------------------
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0030_consumed_media_tables');
