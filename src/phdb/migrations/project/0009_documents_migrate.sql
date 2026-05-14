-- Migration 0009 — Move DigitalDocument rows from messages to documents
-- Created: 2026-05-14
--
-- Moves all schema_type='DigitalDocument' rows from messages into the new
-- documents typed table. Repoints chunks.source_table from 'messages' to
-- 'documents' with corrected source_id. Cleans up orphan threads.
--
-- Bucket recovery: google_drive smuggles bucket into sender_name (clean value).
-- thread_key has source-prefixed values (e.g., "google-drive:My Files") so
-- sender_name is preferred. No OneDrive rows exist yet (gated on this migration).
-- apple_notes_full and staged_md have no meaningful bucket in sender_name (NULL).
--
-- Pre-migration verification (run these before applying):
--   SELECT COUNT(*) FROM messages WHERE schema_type = 'DigitalDocument';
--   SELECT COUNT(*) FROM chunks WHERE source_table = 'messages'
--     AND source_id IN (SELECT id FROM messages WHERE schema_type = 'DigitalDocument');
--
-- Rollback: restore from pre-0007 backup. Partial application leaves the DB
-- in an inconsistent state — don't attempt in-place fix.

-- 1. Copy DigitalDocument rows into documents with honest column mapping
INSERT INTO documents (
    schema_type, rfc822_message_id, subject,
    body_text, body_text_source, body_text_hash, raw_hash,
    is_bulk, source_file_id, mtime,
    bucket
)
SELECT
    m.schema_type,
    m.rfc822_message_id,
    m.subject,
    m.body_text,
    m.body_text_source,
    m.body_text_hash,
    m.raw_hash,
    m.is_bulk,
    m.source_file_id,
    m.date_sent,
    COALESCE(
        NULLIF(m.sender_name, 'Me'),
        (SELECT t.thread_key FROM threads t
         JOIN message_threads mt ON mt.thread_id = t.id
         WHERE mt.message_id = m.id
         LIMIT 1)
    )
FROM messages m
WHERE m.schema_type = 'DigitalDocument';

-- 2. Build temp mapping of old messages.id → new documents.id
CREATE TEMP TABLE _doc_id_map AS
SELECT m.id AS old_id, d.id AS new_id
FROM messages m
JOIN documents d ON d.source_file_id = m.source_file_id
                AND d.raw_hash = m.raw_hash
WHERE m.schema_type = 'DigitalDocument';

-- 3. Repoint chunks from messages to documents
UPDATE chunks
SET source_table = 'documents',
    source_id = (SELECT new_id FROM _doc_id_map WHERE old_id = chunks.source_id)
WHERE source_table = 'messages'
  AND source_id IN (SELECT old_id FROM _doc_id_map);

-- 4. Clean up temp table
DROP TABLE _doc_id_map;

-- 5. Delete migrated rows from messages (CASCADE removes message_threads links)
DELETE FROM messages WHERE schema_type = 'DigitalDocument';

-- 6. Remove orphan threads that lost all their messages
DELETE FROM threads
WHERE id IN (
    SELECT t.id FROM threads t
    LEFT JOIN message_threads mt ON mt.thread_id = t.id
    WHERE mt.message_id IS NULL
      AND t.source_kind IN ('google_drive', 'onedrive', 'apple_notes_full', 'staged_md')
);

-- Post-migration verification:
--   SELECT COUNT(*) FROM documents;  -- should equal pre-migration DigitalDocument count
--   SELECT COUNT(*) FROM chunks WHERE source_table = 'documents';  -- chunk count for migrated rows
--   SELECT COUNT(*) FROM chunks WHERE source_table = 'messages'
--     AND source_id NOT IN (SELECT id FROM messages);  -- should be 0 (no orphan chunks)
--   SELECT COUNT(*) FROM messages WHERE schema_type = 'DigitalDocument';  -- should be 0

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0009_documents_migrate');
