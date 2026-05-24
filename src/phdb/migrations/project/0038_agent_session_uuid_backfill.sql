-- Migration 0038 — backfill session_uuid for Claude Code agent sub-sessions
-- Created: 2026-05-24
--
-- Follow-up to migration 0010 (source_files.session_uuid + dedup cleanup).
-- That migration backfilled session_uuid for top-level Claude Code sessions
-- (filename ending in <UUID>.jsonl) but deliberately left agent sub-sessions
-- at NULL because their filename pattern is `agent-<hex>.jsonl` — they have
-- no parseable UUID and the migration didn't yet treat them as first-class
-- session identifiers.
--
-- The asymmetry leaves the partial UNIQUE index
-- (idx_source_files_kind_session_uuid ON (source_kind, session_uuid)
-- WHERE session_uuid IS NOT NULL) inactive for 214 of 382 claude-code rows,
-- so a future path relocation of those agent files would re-trip the same
-- duplicate-ingest rake that 0010 was authored to clean up.
--
-- This migration:
--   1. Extracts the `agent-<hex>` identifier from the source_path tail and
--      stores it as session_uuid. The identifier is the unique tail token
--      Claude Code generates per agent dispatch; it is unique across both
--      top-level sessions and other agent dispatches within the same cwd.
--   2. Bounded to rows where session_uuid IS NULL — re-runnable, idempotent.
--   3. The companion application code change updates
--      phdb.core.source_files.register_source_file to accept session_uuid as
--      a parameter and adds a second ON CONFLICT clause for
--      (source_kind, session_uuid) so a future relocation of an agent file
--      updates the existing row rather than failing or producing a duplicate.
--
-- PRE-MIGRATION VERIFICATION:
--   SELECT COUNT(*) FROM source_files
--     WHERE source_kind='claude-code'
--       AND session_uuid IS NULL
--       AND source_path LIKE '%agent-%';                     -- expect 214
--   SELECT COUNT(*) FROM source_files
--     WHERE source_kind='claude-code' AND session_uuid IS NOT NULL;  -- expect 168
--
-- POST-MIGRATION VERIFICATION:
--   SELECT COUNT(*) FROM source_files
--     WHERE source_kind='claude-code' AND session_uuid IS NULL;    -- expect 0
--   SELECT COUNT(*) FROM source_files
--     WHERE source_kind='claude-code' AND session_uuid IS NOT NULL;  -- expect 382
--   SELECT COUNT(*) FROM source_files
--     WHERE source_kind='claude-code'
--       AND session_uuid LIKE 'agent-%';                            -- expect 214
--   -- Confirm partial index now protects agent rows:
--   INSERT INTO source_files (source_path, source_kind, file_kind, session_uuid)
--     VALUES ('dummy-agent', 'claude-code', 'jsonl',
--             (SELECT session_uuid FROM source_files
--                WHERE source_kind='claude-code'
--                  AND session_uuid LIKE 'agent-%' LIMIT 1));
--   -- Expected: UNIQUE constraint failed: source_files.source_kind, source_files.session_uuid

-- ============================================================================
-- 1. Backfill session_uuid for agent sub-sessions
--
-- The agent identifier shape is `agent-` followed by a run of hex chars;
-- observed length on disk is 17 hex chars (`agent-` + 17 = 23 chars total).
-- The SQL pattern below is permissive about the hex length but anchored on
-- the `agent-` prefix and the `.jsonl` extension to avoid false matches.
-- ============================================================================
UPDATE source_files
SET session_uuid = REPLACE(
    substr(source_path,
           instr(source_path, 'agent-'),
           length(source_path) - instr(source_path, 'agent-') + 1),
    '.jsonl', ''
)
WHERE source_kind = 'claude-code'
  AND file_kind = 'jsonl'
  AND session_uuid IS NULL
  AND source_path LIKE '%agent-%.jsonl';

-- ============================================================================
-- 2. Record this migration as applied
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id)
VALUES ('0038_agent_session_uuid_backfill');
