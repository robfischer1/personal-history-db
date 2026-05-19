-- Migration 0013 — Create articles typed table for Article (@type) rows
-- Created: 2026-05-18
--
-- Second @type domain to get its own typed table (after documents/0008).
-- Receives rows whose schema_type='Article' — saved web articles, currently
-- the Resources/Articles/ vault folder (219 files) under the Articles
-- Dissolution Pilot. Columns are derived empirically from the live corpus's
-- frontmatter, not from SCHEMA.md §5.10 (which is stale — see plan Phase 6).
--
-- Follows the bookmarks/connections/documents typed-table precedent:
-- own table + domain-specific columns + (source_file_id, raw_hash) dedup index.
-- @context is constant ('https://schema.org') and not stored per-row, per the
-- existing schema_type-only convention.
--
-- Rollback: DROP TABLE articles;

CREATE TABLE IF NOT EXISTS articles (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'Article',
    subject           TEXT,             -- frontmatter `name` (article title)
    url               TEXT,             -- frontmatter `url` — source link
    publisher         TEXT,             -- frontmatter `publisher`
    creator           TEXT,             -- frontmatter `creator`
    description       TEXT,             -- frontmatter `description`
    image_url         TEXT,             -- frontmatter `image`
    categories        TEXT,             -- frontmatter `categories` — JSON array
    tags              TEXT,             -- frontmatter `tags` — JSON array
    aliases           TEXT,             -- frontmatter `aliases` — JSON array
    note_type         TEXT,             -- frontmatter `note_type` ('source-material')
    author_type       TEXT,             -- frontmatter `author_type` ('external')
    file_path         TEXT,             -- relative filename — wikilink-resolution key
    file_size         INTEGER,
    ctime             TEXT,             -- frontmatter `created`
    mtime             TEXT,             -- frontmatter `updated`
    body_text         TEXT,             -- article prose (heading stripped)
    body_text_source  TEXT,
    body_text_hash    TEXT,
    raw_hash          TEXT,
    bucket            TEXT,             -- logical group ('Articles')
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_dedup ON articles(source_file_id, raw_hash);
CREATE INDEX        IF NOT EXISTS idx_articles_path  ON articles(file_path);
CREATE INDEX        IF NOT EXISTS idx_articles_url   ON articles(url);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0013_articles_table');
