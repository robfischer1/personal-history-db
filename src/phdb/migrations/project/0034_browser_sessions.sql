-- Migration 0034: browser_sessions + session_tabs
--
-- Session Buddy nxs.json.v2 export schema.
-- Two tables:
--   browser_sessions — one row per snapshot or saved collection
--   session_tabs     — one row per tab/link within a session
--
-- Design notes:
--   - session_type: 'snapshot-scheduled' | 'browser-closed' | 'collection'
--   - timestamp stores Unix milliseconds from Session Buddy's id field
--     (history entries) or created field (collections)
--   - source_id is the Session Buddy-assigned string id; used for dedup
--   - DON'T dedup snapshots — each snapshot is a discrete cognitive state;
--     a tab appearing across N snapshots is N intervals of dwell time

CREATE TABLE IF NOT EXISTS browser_sessions (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT    NOT NULL DEFAULT 'BrowserSession',
    session_type    TEXT    NOT NULL,              -- snapshot-scheduled | browser-closed | collection
    timestamp       INTEGER,                       -- Unix ms (from Session Buddy id / created)
    window_count    INTEGER,
    tab_count       INTEGER,
    source_file     TEXT,                          -- original export file path
    source_id       TEXT,                          -- Session Buddy id for dedup
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS session_tabs (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT    NOT NULL DEFAULT 'SessionTab',
    session_id      INTEGER REFERENCES browser_sessions(id),
    window_index    INTEGER,                       -- 0-based window order within snapshot
    tab_index       INTEGER,                       -- 0-based tab order within window
    url             TEXT,
    title           TEXT,
    active          BOOLEAN,                       -- tab was marked active in the snapshot
    fav_icon_url    TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Dedup index on source_id: prevents duplicate sessions from the same export
CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_sessions_source_id
    ON browser_sessions(source_id)
    WHERE source_id IS NOT NULL;

-- FK traversal: tabs → session
CREATE INDEX IF NOT EXISTS idx_session_tabs_session_id
    ON session_tabs(session_id);

-- Time-range queries on snapshots
CREATE INDEX IF NOT EXISTS idx_browser_sessions_timestamp
    ON browser_sessions(timestamp);

-- URL presence queries (dwell-time signal)
CREATE INDEX IF NOT EXISTS idx_session_tabs_url
    ON session_tabs(url);

INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0034_browser_sessions');
