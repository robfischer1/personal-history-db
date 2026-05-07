-- Migration 002 — generalize Conversation beyond Gmail
-- Created: 2026-05-02
--
-- Adds source-agnostic dedup keys so the threads table can absorb SMS/iMessage
-- group chats and future IM logs (MSN/AIM/Trillian/Yahoo) without bolting on
-- per-source columns.
--
-- threads.gmail_thread_id is preserved for backwards compatibility but is no
-- longer the unique-key path; (source_kind, thread_key) is.

-- ============================================================================
-- threads — add source_kind + thread_key
-- ============================================================================
ALTER TABLE threads ADD COLUMN source_kind TEXT;
ALTER TABLE threads ADD COLUMN thread_key  TEXT;

-- Backfill existing Gmail rows
UPDATE threads
   SET source_kind = 'gmail',
       thread_key  = gmail_thread_id
 WHERE gmail_thread_id IS NOT NULL
   AND source_kind IS NULL;

-- New unique index on the source-agnostic identity
CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_kind_key
    ON threads(source_kind, thread_key)
 WHERE thread_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_threads_source_kind
    ON threads(source_kind);

-- ============================================================================
-- source_files — add source_kind alongside file_kind (different dimensions)
--   file_kind   = format ('mbox', 'sqlite', 'csv', 'html', 'json', 'xml')
--   source_kind = origin ('gmail', 'imessage', 'msn', 'aim', 'yahoo', 'icloud-notes', etc.)
-- ============================================================================
ALTER TABLE source_files ADD COLUMN source_kind TEXT;

-- Backfill: existing Gmail mbox row
UPDATE source_files
   SET source_kind = 'gmail'
 WHERE source_org  = 'Google Takeout'
   AND file_kind   = 'mbox'
   AND source_kind IS NULL;

CREATE INDEX IF NOT EXISTS idx_source_files_source_kind
    ON source_files(source_kind);

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('002_conversation_generalization');
