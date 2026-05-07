-- Migration 005 — Connections (Facebook friends graph + future: LinkedIn etc.)
-- Created: 2026-05-06
--
-- Schema-blessed core: BefriendAction (Schema.org subtype of InteractAction → Action).
-- "The act of forming a personal connection with someone (object) mutually/bidirectionally/symmetrically."
--
-- Single-table model parallels `bookmarks` (migration 004): one row per
-- (dedupe_key, instrument). The `instrument` column identifies the platform
-- ('facebook' for now; 'linkedin' / etc. reserved).
--
-- Identity:
--   `dedupe_key` = profile_url if present, else 'name:'||name_normalized.
--   Modern FB takeouts (2026-04-17 confirmed) emit `<h2>Name</h2>` only — no
--   anchor href, no profile URL. Therefore name-keyed matching is the practical
--   default; profile_url columns stay in the schema for older exports that may
--   include them and for future format restoration.
--
-- Status:
--   connection_status ∈ {active, inactive, pending_outbound, pending_inbound, rejected}
--   `active` ← your_friends.html
--   `inactive` ← removed_friends.html OR inferred (was active in older export, missing from latest)
--   `pending_outbound` ← sent_friend_requests.html
--   `pending_inbound` ← received_friend_requests.html
--   `rejected` ← rejected_friend_requests.html
--
-- Reconciliation (across exports):
--   - status ← latest sighting wins (most recent export_date)
--   - friends_since ← earliest non-null across all sightings
--   - appearances_json ← full audit trail of per-export observations
--
-- Person-note reconciliation (display_name → Entities/People/) is OUT OF SCOPE
-- for migration 005. `person_link` left null pending a separate session.

-- ============================================================================
-- connections — one row per (dedupe_key × instrument)
-- ============================================================================
CREATE TABLE IF NOT EXISTS connections (
    id                       INTEGER PRIMARY KEY,
    schema_type              TEXT NOT NULL DEFAULT 'BefriendAction',  -- Schema.org @type

    -- Platform
    instrument               TEXT NOT NULL,        -- 'facebook' (reserved: 'linkedin', etc.)

    -- Identity
    dedupe_key               TEXT NOT NULL,        -- profile_url if available, else 'name:'||name_normalized
    profile_url              TEXT,                 -- canonical profile URL when extractable
    profile_id               TEXT,                 -- numeric ID (FB /profile.php?id=N) when extractable
    vanity_slug              TEXT,                 -- vanity slug (FB /someone) when extractable
    display_name             TEXT NOT NULL,        -- name as shown in latest export
    name_normalized          TEXT NOT NULL,        -- NFKD-stripped, lowercased, whitespace-collapsed

    -- Vault reconciliation (deferred — null on initial ingest)
    person_link              TEXT,                 -- [[Wikilink]] to Entities/People/<Name>.md

    -- Connection state
    connection_status        TEXT NOT NULL CHECK(connection_status IN
                                                 ('active','inactive',
                                                  'pending_outbound','pending_inbound',
                                                  'rejected')),
    inactive_reason          TEXT,                 -- 'removed_friends_file','missing_from_latest_export', null
    friends_since            TEXT,                 -- ISO date (earliest non-null across all sightings)
    friends_since_source     TEXT,                 -- which export contributed friends_since

    -- Provenance (per-instrument tracking, mirrors bookmarks pattern)
    first_seen_export        TEXT NOT NULL,        -- export_id of earliest sighting
    last_seen_export         TEXT NOT NULL,        -- export_id of most-recent sighting
    last_seen_at             TEXT NOT NULL,        -- export_date of most-recent sighting (ISO)
    appearance_count         INTEGER NOT NULL DEFAULT 1,
    appearances_json         TEXT,                 -- JSON: [{export_id, export_date, file, status, friends_since, raw_name}]
    source_file_id           INTEGER REFERENCES source_files(id),
    raw_hash                 TEXT,

    ingested_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Identity: one row per (dedupe_key × instrument).
CREATE UNIQUE INDEX IF NOT EXISTS idx_connections_dedupe_instrument
    ON connections(instrument, dedupe_key);

-- Common query paths.
CREATE INDEX IF NOT EXISTS idx_connections_instrument        ON connections(instrument);
CREATE INDEX IF NOT EXISTS idx_connections_status            ON connections(connection_status);
CREATE INDEX IF NOT EXISTS idx_connections_name_normalized   ON connections(name_normalized);
CREATE INDEX IF NOT EXISTS idx_connections_profile_url       ON connections(profile_url);
CREATE INDEX IF NOT EXISTS idx_connections_friends_since     ON connections(friends_since);

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0005_connections');
