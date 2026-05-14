-- Migration 0008 — Create documents typed table for DigitalDocument rows
-- Created: 2026-05-14
--
-- First typed table in the schema-reshape initiative. Receives rows whose
-- schema_type='DigitalDocument' — files from Google Drive, OneDrive, Apple
-- Notes (full), staged markdown. Columns are honest: file_path/mtime/ctime
-- instead of vestigial sender_address/direction that were lies.
--
-- The embedding chunk registry is now "chunks" (migration 0007).
-- This table is the SOURCE for chunks, not the chunk registry itself.
--
-- Rollback: DROP TABLE documents; then restore from backup if rows were
-- already migrated (0009).

CREATE TABLE IF NOT EXISTS documents (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'DigitalDocument',
    rfc822_message_id TEXT,
    subject           TEXT,
    file_path         TEXT,
    file_size         INTEGER,
    mtime             TEXT,
    ctime             TEXT,
    body_text         TEXT,
    body_text_source  TEXT,
    body_text_hash    TEXT,
    raw_hash          TEXT,
    is_bulk           INTEGER NOT NULL DEFAULT 0,
    source_file_id    INTEGER REFERENCES source_files(id),
    bucket            TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_dedup  ON documents(source_file_id, raw_hash);
CREATE INDEX        IF NOT EXISTS idx_documents_path   ON documents(file_path);
CREATE INDEX        IF NOT EXISTS idx_documents_bucket ON documents(bucket);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0008_documents_typed_table');
