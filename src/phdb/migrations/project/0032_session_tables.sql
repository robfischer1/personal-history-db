-- Migration 0032 — Create sessions + session_events tables, add predicates
-- Created: 2026-05-23
--
-- Session-Close Dissolution (Outputs/Plans/Session-Close Dissolution.md).
-- Two tables: `sessions` (one row per Claude session) and `session_events`
-- (many rows per session, FK to sessions). Event types use English names:
-- decision, reversal, tension, pushback, file_touched, commit,
-- question_asked. Session-level summary lives as a column on `sessions`,
-- not as an event row.
--
-- Also adds 6 predicates for session-event relationships:
-- occurredDuring/containsEvent, touchedFile, decidedOn, reversedBy/reverses.
--
-- Rollback: DROP TABLE session_events; DROP TABLE sessions;
--           DELETE FROM predicates WHERE id BETWEEN 37 AND 42;

-- ---- sessions ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'Session',
    session_key     TEXT NOT NULL,       -- natural key: "2026-05-23a"
    environment     TEXT,                -- "code" or "cowork"
    start_ts        TEXT,                -- ISO 8601
    end_ts          TEXT,                -- ISO 8601
    model           TEXT,                -- e.g. "claude-opus-4-6"
    handoff_suffix  TEXT,                -- "a", "b", ... "aa", etc.
    session_summary TEXT,                -- narrative digest (Phase 0 Q3)
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_key
    ON sessions(session_key);
CREATE INDEX IF NOT EXISTS idx_sessions_start
    ON sessions(start_ts);

-- ---- session_events ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS session_events (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'SessionEvent',
    session_id      INTEGER REFERENCES sessions(id),
    event_type      TEXT NOT NULL,       -- decision, reversal, tension, pushback,
                                         -- file_touched, commit, question_asked
    ts              TEXT,                -- ISO 8601, when event occurred
    payload         TEXT,                -- JSON blob with type-specific data
    file_path       TEXT,                -- for file_touched events
    commit_sha      TEXT,                -- for commit events
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_events_dedup
    ON session_events(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_session_events_session
    ON session_events(session_id);
CREATE INDEX IF NOT EXISTS idx_session_events_type
    ON session_events(event_type);
CREATE INDEX IF NOT EXISTS idx_session_events_ts
    ON session_events(ts);

-- ---- Predicates for session-event relationships ------------------------------

INSERT OR IGNORE INTO predicates (id, name, symmetric, description) VALUES
    (37, 'occurredDuring', 0, 'Event occurred during a session'),
    (38, 'containsEvent',  0, 'Session contains an event'),
    (39, 'touchedFile',    0, 'Event touched a file path'),
    (40, 'decidedOn',      0, 'Decision event was about a topic'),
    (41, 'reversedBy',     0, 'Claim or decision was reversed by a reversal event'),
    (42, 'reverses',       0, 'Reversal event reverses a prior claim or decision');

-- Wire inverse pairs
UPDATE predicates SET inverse_predicate_id = 38 WHERE id = 37;
UPDATE predicates SET inverse_predicate_id = 37 WHERE id = 38;
UPDATE predicates SET inverse_predicate_id = 42 WHERE id = 41;
UPDATE predicates SET inverse_predicate_id = 41 WHERE id = 42;

-- ---- Registration ------------------------------------------------------------
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0032_session_tables');
