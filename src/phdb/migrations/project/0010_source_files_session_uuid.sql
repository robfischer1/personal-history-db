-- Migration 0010 — source_files.session_uuid + AI-sessions dedup cleanup
-- Created: 2026-05-16
--
-- Adds the session_uuid column to source_files, backfills it for existing
-- jsonl rows from the filename, creates a partial UNIQUE index on
-- (source_kind, session_uuid) so the same Claude Code session can't be
-- re-ingested under a renamed path, and cleans up the 18 legacy
-- C:\Users\<owner>\.claude\projects\ source_files whose counterpart already
-- exists under D:\<records>\AI Sessions\Claude\.
--
-- BACKGROUND
-- ----------
-- The ingester's only source-file dedup key is `source_path`. When Rob
-- relocated the Claude Code session archive from
--   C:\Users\<owner>\.claude\projects\<encoded-cwd>\<session-uuid>.jsonl
-- to
--   D:\<records>\AI Sessions\Claude\claude-code__<encoded-cwd>__<session-uuid>.jsonl
-- and the next ingest pass scanned the new path, 18 sessions ended up
-- registered twice (once per path), and their messages were inserted
-- twice (same raw_hash, different source_file_id — the message-level
-- UNIQUE INDEX is on (source_file_id, raw_hash), so cross-source duplicates
-- aren't caught).
--
-- 17 of the 18 dup pairs have identical message_count; the 18th
-- (session_uuid=4efecc8b-d706-4667-b922-7476858b2991, the live
-- c--Users-<owner>-Obsidian session) has the relocated copy as a strict
-- superset (783 msgs vs 775) — that session kept being used after the
-- 2026-05-08 ingest, and the message UUIDs are deterministic, so the
-- relocated source_file already contains every turn from the legacy one.
-- Deletion is therefore safe across all 18 pairs.
--
-- D:\<records>\AI Sessions\Claude\ is the canonical AI-sessions path going
-- forward (Records pillar, preserve-but-desk-only per
-- feedback_records_tier_distinction). The claude_code adapter is patched
-- (separate commit) to reject C:\Users\<owner>\.claude\ paths.
--
-- IDEMPOTENCY / RE-RUN SAFETY
-- ---------------------------
-- - ALTER ADD COLUMN is one-shot; running this migration twice will fail
--   on the ALTER, which is correct (schema_migrations gates it).
-- - The UPDATE backfill is bounded to rows where session_uuid IS NULL, so
--   it can be re-run.
-- - The DELETE FROM messages / DELETE FROM source_files use exact paths;
--   if the legacy rows are already gone, the DELETE matches zero rows.
--
-- ROLLBACK
-- --------
-- SQLite ALTER TABLE DROP COLUMN is supported (3.35+); to roll back:
--   DROP INDEX IF EXISTS idx_source_files_kind_session_uuid;
--   ALTER TABLE source_files DROP COLUMN session_uuid;
-- The deleted messages cannot be recovered without restoring from the
-- pre-migration .gz snapshot at personal-history.db.gz — make sure the
-- pre-0010 backup exists before applying.
--
-- PRE-MIGRATION VERIFICATION (run these on <host> before applying):
--   SELECT COUNT(*) FROM source_files WHERE file_kind='jsonl';                  -- expect 400
--   SELECT COUNT(*) FROM source_files
--     WHERE source_path LIKE 'C:\Users\<owner>\.claude\%' AND file_kind='jsonl';  -- expect 18
--   SELECT COUNT(*) FROM messages
--     WHERE source_file_id IN
--       (SELECT id FROM source_files WHERE source_path LIKE 'C:\Users\<owner>\.claude\%');
--   -- expect roughly the same number that lives under the D:\<records> counterparts;
--   -- exact total verified before/after via the post-verification block below.

-- ============================================================================
-- 1. Add the session_uuid column
-- ============================================================================
ALTER TABLE source_files ADD COLUMN session_uuid TEXT;

-- ============================================================================
-- 2. Backfill session_uuid from source_path for existing jsonl rows
--
-- The filename pattern at the tail of source_path is `<uuid>.jsonl` for
-- regular sessions (36-char UUID = 8-4-4-4-12 hex with dashes). Agent
-- sub-sessions use `agent-<hex>.jsonl` and won't match the GLOB below —
-- those keep session_uuid NULL and continue to dedup on source_path.
-- ============================================================================
UPDATE source_files
SET session_uuid = lower(substr(source_path, -42, 36))
WHERE file_kind = 'jsonl'
  AND session_uuid IS NULL
  AND substr(source_path, -42, 36) GLOB
    '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]'
    || '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]-'
    || '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]-'
    || '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]-'
    || '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]-'
    || '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]'
    || '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]'
    || '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]';

-- ============================================================================
-- 3. Delete child messages for legacy duplicates (only where a relocated
--    counterpart exists, to be defensive — never orphan a unique session)
-- ============================================================================
DELETE FROM messages
WHERE source_file_id IN (
    SELECT sf_legacy.id
    FROM source_files sf_legacy
    INNER JOIN source_files sf_relocated
      ON sf_legacy.session_uuid = sf_relocated.session_uuid
     AND sf_legacy.source_kind  = sf_relocated.source_kind
     AND sf_legacy.id != sf_relocated.id
    WHERE sf_legacy.source_path    LIKE 'C:\Users\<owner>\.claude\%'
      AND sf_relocated.source_path LIKE 'D:\<records>\AI Sessions\Claude\%'
      AND sf_legacy.session_uuid IS NOT NULL
);

-- ============================================================================
-- 4. Delete the legacy source_files rows themselves
-- ============================================================================
DELETE FROM source_files
WHERE id IN (
    SELECT sf_legacy.id
    FROM source_files sf_legacy
    INNER JOIN source_files sf_relocated
      ON sf_legacy.session_uuid = sf_relocated.session_uuid
     AND sf_legacy.source_kind  = sf_relocated.source_kind
     AND sf_legacy.id != sf_relocated.id
    WHERE sf_legacy.source_path    LIKE 'C:\Users\<owner>\.claude\%'
      AND sf_relocated.source_path LIKE 'D:\<records>\AI Sessions\Claude\%'
      AND sf_legacy.session_uuid IS NOT NULL
);

-- ============================================================================
-- 5. Partial UNIQUE index — same (source_kind, session_uuid) can't repeat
--    when session_uuid is non-NULL. Rows with NULL session_uuid (older
--    sources, agent sub-sessions) keep using source_path for dedup.
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_files_kind_session_uuid
    ON source_files (source_kind, session_uuid)
    WHERE session_uuid IS NOT NULL;

-- ============================================================================
-- POST-VERIFICATION (run these after the migration applies):
--   SELECT COUNT(*) FROM source_files WHERE file_kind='jsonl';                  -- expect 382 (was 400, -18)
--   SELECT COUNT(*) FROM source_files
--     WHERE source_path LIKE 'C:\Users\<owner>\.claude\%' AND file_kind='jsonl';  -- expect 0
--   SELECT COUNT(*) FROM source_files
--     WHERE file_kind='jsonl' AND session_uuid IS NOT NULL;                     -- expect 168 (unique session UUIDs)
--   SELECT COUNT(*) FROM source_files
--     WHERE file_kind='jsonl' AND session_uuid IS NULL;                         -- expect 214 (agent sub-sessions)
--   -- Confirm the partial index works (should error on duplicate insert):
--   INSERT INTO source_files (source_path, source_kind, file_kind, session_uuid)
--     VALUES ('dummy', 'claude-code', 'jsonl',
--             (SELECT session_uuid FROM source_files
--                WHERE source_kind='claude-code' AND session_uuid IS NOT NULL LIMIT 1));
--   -- Expected: UNIQUE constraint failed: source_files.source_kind, source_files.session_uuid

-- ============================================================================
-- 6. Record this migration as applied
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0010_source_files_session_uuid');
