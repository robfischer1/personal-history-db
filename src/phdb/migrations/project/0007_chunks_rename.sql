-- Migration 0007 — Rename documents (chunk registry) to chunks
-- Created: 2026-05-14
--
-- The "documents" table is actually an embedding chunk registry keyed by
-- (source_table, source_id, chunk_index). Renaming it to "chunks" frees
-- the "documents" name for the new DigitalDocument typed table (0008).
--
-- Steps:
--   1. Drop FTS triggers (they'll be recreated with new names)
--   2. ALTER TABLE documents RENAME TO chunks
--   3. Drop + recreate indexes with chunks_ prefix
--   4. Drop + recreate FTS5 virtual table with content='chunks'
--   5. Repopulate FTS from chunks
--   6. Recreate triggers on chunks
--
-- vec0 doc_vectors is rowid-based (no table name reference) — unaffected.
--
-- Rollback: restore from pre-0007 backup. In-place rollback would require
-- reversing the rename + FTS rebuild — not worth the complexity.

-- 1. Drop old triggers
DROP TRIGGER IF EXISTS documents_ai;
DROP TRIGGER IF EXISTS documents_ad;
DROP TRIGGER IF EXISTS documents_au;

-- 2. Rename table
ALTER TABLE documents RENAME TO chunks;

-- 3. Drop old indexes, create new ones
DROP INDEX IF EXISTS idx_documents_source;
DROP INDEX IF EXISTS idx_documents_schema_type;
DROP INDEX IF EXISTS idx_documents_embedded_at;
DROP INDEX IF EXISTS idx_documents_src_chunk;

CREATE INDEX        idx_chunks_source       ON chunks(source_table, source_id);
CREATE INDEX        idx_chunks_schema_type  ON chunks(schema_type);
CREATE INDEX        idx_chunks_embedded_at  ON chunks(embedded_at);
CREATE UNIQUE INDEX idx_chunks_src_chunk    ON chunks(source_table, source_id, chunk_index);

-- 4. Drop old FTS5 virtual table, recreate with content='chunks'
DROP TABLE IF EXISTS doc_fts;

CREATE VIRTUAL TABLE doc_fts USING fts5(
    content,
    title,
    content='chunks',
    content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);

-- 5. Repopulate FTS from chunks (bulk insert — expect ~30s on 220K+ rows)
INSERT INTO doc_fts(rowid, content, title)
SELECT id, content, title FROM chunks;

-- 6. Create new triggers on chunks
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO doc_fts(rowid, content, title) VALUES (new.id, new.content, new.title);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO doc_fts(doc_fts, rowid, content, title) VALUES ('delete', old.id, old.content, old.title);
END;
CREATE TRIGGER chunks_au AFTER UPDATE OF content, title ON chunks BEGIN
    INSERT INTO doc_fts(doc_fts, rowid, content, title) VALUES ('delete', old.id, old.content, old.title);
    INSERT INTO doc_fts(rowid, content, title)         VALUES (new.id, new.content, new.title);
END;

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0007_chunks_rename');
