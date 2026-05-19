-- Migration 0015 — writing delta-stream capture
-- Created: 2026-05-19
--
-- Stores the per-keystroke / per-transaction editing event stream emitted by
-- the `obsidian-delta-stream` plugin (~/Obsidian/obsidian-delta-stream/).
-- The plugin captures CodeMirror 6 ViewUpdate transactions as append-only
-- NDJSON in `~/Obsidian/delta-stream-data/YYYY-MM-DD.ndjson`. The adapter
-- (`phdb.adapters.writing_deltas`) parses those files into:
--
--   writing_sessions — one row per writing session (start → end boundary)
--   writing_deltas   — one row per `doc-change` or `selection-change` event
--
-- `note-switch` events from the NDJSON are not materialised here — they live
-- on disk for future inspection but are not load-bearing for queries.
--
-- Aggregates on writing_sessions (doc_change_count, total_inserted_chars,
-- undo_count, …) are computed by the adapter via UPDATE after delta inserts
-- so queries don't have to GROUP BY every time.
--
-- Typed columns per `feedback_typed_columns_over_json`. The single JSON-array
-- column is `selection_ranges_json` — the narrow list-valued field allowance.
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS writing_deltas;
--   DROP TABLE IF EXISTS writing_sessions;

-- ============================================================================
-- 1. writing_sessions — one row per writing session
-- ============================================================================
CREATE TABLE IF NOT EXISTS writing_sessions (
    id                      INTEGER PRIMARY KEY,
    schema_type             TEXT NOT NULL DEFAULT 'WritingSession',  -- forward-compat with SCHEMA.md §5.x once materialised

    -- Identity
    session_id              TEXT NOT NULL UNIQUE,  -- e.g. s_mpd6sbe7_1c65e2 (plugin-emitted)

    -- Note context at session start
    note_path               TEXT NOT NULL,         -- vault-relative POSIX path
    vault_folder            TEXT,                  -- denormalised parent folder
    note_type               TEXT,                  -- frontmatter @type at session start, if any

    -- Bounds
    started_at              INTEGER NOT NULL,      -- epoch milliseconds
    ended_at                INTEGER,               -- epoch milliseconds; NULL if session never observed an end
    ended_reason            TEXT,                  -- 'idle' | 'blur' | 'note-switch' | 'unload'

    -- Aggregates (computed at ingest by the adapter; cheap denormalisation)
    doc_change_count        INTEGER NOT NULL DEFAULT 0,
    selection_change_count  INTEGER NOT NULL DEFAULT 0,
    insert_count            INTEGER NOT NULL DEFAULT 0,    -- doc-changes with non-empty insertedText
    delete_count            INTEGER NOT NULL DEFAULT 0,    -- doc-changes with non-empty deletedText
    total_inserted_chars    INTEGER NOT NULL DEFAULT 0,
    total_deleted_chars     INTEGER NOT NULL DEFAULT 0,
    undo_count              INTEGER NOT NULL DEFAULT 0,    -- doc-changes with userEvent='undo'
    paste_count             INTEGER NOT NULL DEFAULT 0,    -- doc-changes with userEvent='input.paste'

    -- Provenance
    source_file_id          INTEGER REFERENCES source_files(id),
    ingested_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_writing_sessions_note_path     ON writing_sessions(note_path);
CREATE INDEX IF NOT EXISTS idx_writing_sessions_started_at    ON writing_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_writing_sessions_vault_folder  ON writing_sessions(vault_folder);
CREATE INDEX IF NOT EXISTS idx_writing_sessions_note_type     ON writing_sessions(note_type);

-- ============================================================================
-- 2. writing_deltas — one row per doc-change OR selection-change event
-- ============================================================================
CREATE TABLE IF NOT EXISTS writing_deltas (
    id                      INTEGER PRIMARY KEY,
    schema_type             TEXT NOT NULL DEFAULT 'WritingDelta',

    -- Relation
    session_pk              INTEGER NOT NULL REFERENCES writing_sessions(id) ON DELETE CASCADE,
    session_id              TEXT NOT NULL,         -- denormalised for queries that don't join

    -- Common
    ts                      INTEGER NOT NULL,      -- epoch milliseconds
    event_type              TEXT NOT NULL CHECK (event_type IN ('doc-change', 'selection-change')),
    note_path               TEXT NOT NULL,         -- denormalised; capture-time active file

    -- doc-change fields (NULL for selection-change events)
    from_a                  INTEGER,               -- offset bounds before the change
    to_a                    INTEGER,
    from_b                  INTEGER,               -- offset bounds after the change
    to_b                    INTEGER,
    inserted_text           TEXT,                  -- empty string for pure deletions
    deleted_text            TEXT,                  -- empty string when settings.captureDeletedText=false
    user_event              TEXT,                  -- CM6 Transaction.userEvent — 'input.type', 'delete.backward', 'input.paste', 'undo', …

    -- selection-change fields (NULL for doc-change events)
    selection_ranges_json   TEXT,                  -- JSON array of {anchor, head}

    -- Provenance / dedup
    source_file_id          INTEGER REFERENCES source_files(id),
    raw_hash                TEXT NOT NULL UNIQUE   -- sha256 of the NDJSON line — makes re-ingest idempotent
);

CREATE INDEX IF NOT EXISTS idx_writing_deltas_session_pk    ON writing_deltas(session_pk);
CREATE INDEX IF NOT EXISTS idx_writing_deltas_ts            ON writing_deltas(ts);
CREATE INDEX IF NOT EXISTS idx_writing_deltas_user_event    ON writing_deltas(user_event);
CREATE INDEX IF NOT EXISTS idx_writing_deltas_event_type    ON writing_deltas(event_type);

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0015_writing_deltas');
