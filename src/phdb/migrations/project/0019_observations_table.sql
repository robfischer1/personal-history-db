-- Migration 0019 — Create observations typed table + migrate Observation rows
-- Created: 2026-05-22
--
-- Phase 1 of Messages Decomposition: 5,968K Observation rows (87% of messages)
-- move to a health-domain table. Preserves message IDs so sidecar FKs
-- (record_metadata.message_id, hr_samples.parent_message_id) continue to work
-- during the transition.
--
-- Post-migration steps (NOT in this file — run after validation):
--   1. Convert message_threads to inThread/threadContains triples (Python script)
--   2. Convert sidecar FKs to hasMetadata/hasHeartRateSample triples (Python script)
--   3. DELETE FROM messages WHERE schema_type = 'Observation'
--   4. DELETE FROM message_threads WHERE message_id IN (SELECT id FROM observations)
--
-- Source adapters: apple-health, apple-health-backup, google-fit
--
-- Rollback: DROP TABLE observations; (messages rows still present until post-migration cleanup)

CREATE TABLE IF NOT EXISTS observations (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'Observation',
    observation_key   TEXT,                  -- dedup key (was rfc822_message_id)
    type_identifier   TEXT,                  -- health metric type (e.g., "calories.bmr")
    subject           TEXT,                  -- full subject line (type + value)
    source_device     TEXT,                  -- platform:user (e.g., "apple-health:Rob")
    direction         TEXT NOT NULL DEFAULT 'self',
    date_observed     TEXT,                  -- ISO 8601 (was date_sent)
    date_end          TEXT,                  -- ISO 8601 end of range (was date_received)
    body_text         TEXT,                  -- structured observation text
    body_text_source  TEXT,                  -- format indicator (e.g., "apple-health-xml")
    body_text_hash    TEXT,
    is_bulk           INTEGER NOT NULL DEFAULT 1,
    bulk_signal       TEXT,
    source_byte_offset INTEGER,
    source_byte_length INTEGER,
    raw_hash          TEXT,
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_dedup
    ON observations(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_observations_type
    ON observations(type_identifier) WHERE type_identifier IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_observations_date
    ON observations(date_observed) WHERE date_observed IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_observations_source
    ON observations(source_device) WHERE source_device IS NOT NULL;

-- Migrate data: preserve message IDs so sidecar FKs still resolve
INSERT INTO observations (
    id, schema_type, observation_key, type_identifier, subject,
    source_device, direction, date_observed, date_end,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, created_at
)
SELECT
    id, schema_type, rfc822_message_id, sender_name, subject,
    sender_address, direction, date_sent, date_received,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, ingested_at
FROM messages
WHERE schema_type = 'Observation';

-- Repoint chunks (only 12 exist for Observation, but be thorough)
UPDATE chunks SET source_table = 'observations'
WHERE source_table = 'messages'
  AND source_id IN (SELECT id FROM observations);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0019_observations_table');
