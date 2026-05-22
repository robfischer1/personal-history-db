-- Migration 0017 — Create clippings typed table
-- Created: 2026-05-21
--
-- Absorbs both References/Clippings/ (@type Quotation) and
-- References/Reddit Posts/ (@type Comment) under one table.
-- Reddit Posts are not differentiated — they dissolve as clippings.
-- Column set mirrors articles (0013) with identical shape.
--
-- Rollback: DROP TABLE clippings;

CREATE TABLE IF NOT EXISTS clippings (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'Quotation',
    subject           TEXT,             -- frontmatter `name`
    url               TEXT,             -- frontmatter `url` — source link
    publisher         TEXT,             -- frontmatter `publisher`
    creator           TEXT,             -- frontmatter `creator`
    description       TEXT,             -- frontmatter `description`
    image_url         TEXT,             -- frontmatter `image`
    categories        TEXT,             -- frontmatter `categories` — JSON array
    tags              TEXT,             -- frontmatter `tags` — JSON array
    aliases           TEXT,             -- frontmatter `aliases` — JSON array
    note_type         TEXT,             -- frontmatter `note_type`
    author_type       TEXT,             -- frontmatter `author_type` ('external')
    file_path         TEXT,             -- relative filename — wikilink-resolution key
    file_size         INTEGER,
    ctime             TEXT,             -- frontmatter `created`
    mtime             TEXT,             -- frontmatter `updated`
    body_text         TEXT,             -- clipping prose (heading stripped)
    body_text_source  TEXT,
    body_text_hash    TEXT,
    raw_hash          TEXT,
    bucket            TEXT,             -- logical group ('Clippings')
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_clippings_dedup ON clippings(source_file_id, raw_hash);
CREATE INDEX        IF NOT EXISTS idx_clippings_path  ON clippings(file_path);
CREATE INDEX        IF NOT EXISTS idx_clippings_url   ON clippings(url);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0017_clippings_table');
