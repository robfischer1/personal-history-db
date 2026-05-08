-- Migration 006 — AI Sessions (claude-code, gemini-web, gemini-scribe adapters)
-- Created: 2026-05-08
--
-- Additive ALTER only. All new columns are nullable with NULL defaults so
-- existing rows (gmail, imessage, discord, etc.) are unaffected.
--
-- messages new columns:
--   kind       — turn type discriminator; NULL on legacy rows
--   role       — speaker role; NULL on legacy rows
--   parent_uuid — parent turn UUID for graph traversal (claude-code: parentUuid field)
--   tool_name  — promoted from payload for Bash/Edit/Read/etc. filters
--   tool_use_id — links tool_use ↔ tool_result pairs by ID
--   model      — per-turn model name (claude-code: model field)
--   payload    — raw source line preserved as JSON (JSONL line or HTML cell)
--
-- threads new columns:
--   metadata   — source-specific JSON: claude-code {gitBranch, cwd, version, userType};
--                gemini-web {branch_key, branch_lineage, parent_thread_key}
--   cwd        — promoted from metadata for index-supported path queries
--
-- kind valid values (enforced at application level, not CHECK constraint):
--   'message'      — a conversational turn (user or assistant)
--   'tool_use'     — assistant requesting a tool call
--   'tool_result'  — tool response returned to assistant
--   'summary'      — claude-code compaction summary node
--   'sidechain'    — claude-code thinking/sidechain block
--   'branch_fork'  — gemini-web synthetic fork marker
--   'system_event' — gemini-web activity events (Created, Gave, etc.)
--   'unknown'      — unrecognized line type; payload preserved for re-derivation
--
-- role valid values: 'user' | 'assistant' | 'system' | NULL

-- ============================================================================
-- messages — 7 new columns for AI session turn structure
-- ============================================================================
ALTER TABLE messages ADD COLUMN kind        TEXT;
ALTER TABLE messages ADD COLUMN role        TEXT;
ALTER TABLE messages ADD COLUMN parent_uuid TEXT;
ALTER TABLE messages ADD COLUMN tool_name   TEXT;
ALTER TABLE messages ADD COLUMN tool_use_id TEXT;
ALTER TABLE messages ADD COLUMN model       TEXT;
ALTER TABLE messages ADD COLUMN payload     TEXT;  -- JSON

-- ============================================================================
-- threads — 2 new columns for AI session metadata
-- ============================================================================
ALTER TABLE threads ADD COLUMN metadata TEXT;  -- JSON
ALTER TABLE threads ADD COLUMN cwd      TEXT;

-- ============================================================================
-- Indexes
-- ============================================================================

-- Filtering turns by kind (e.g. "all tool_use rows") — also covers IS NOT NULL
CREATE INDEX IF NOT EXISTS idx_messages_kind
    ON messages(kind)
 WHERE kind IS NOT NULL;

-- Composite for AI session content queries: kind + date without a source join
CREATE INDEX IF NOT EXISTS idx_messages_kind_date
    ON messages(kind, date_sent)
 WHERE kind IS NOT NULL;

-- Graph traversal: find children of a parent turn
CREATE INDEX IF NOT EXISTS idx_messages_parent_uuid
    ON messages(parent_uuid)
 WHERE parent_uuid IS NOT NULL;

-- Tool-chain reconstruction: tool_use ↔ tool_result pairing
CREATE INDEX IF NOT EXISTS idx_messages_tool_use_id
    ON messages(tool_use_id)
 WHERE tool_use_id IS NOT NULL;

-- "Show me all sessions touching path X"
CREATE INDEX IF NOT EXISTS idx_threads_cwd
    ON threads(cwd)
 WHERE cwd IS NOT NULL;

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0006_ai_sessions');
