-- Migration 003 — Apple Health + Google Timeline sidecars
-- Created: 2026-05-03
--
-- Adds sidecar tables for high-volume time-series telemetry that doesn't
-- belong in `messages` (per-second HR samples, per-record metadata, workout
-- statistics, geo trajectory points).
--
-- Also adds a UNIQUE INDEX on (source_file_id, raw_hash) so reruns of any
-- ingester are idempotent without bespoke resumability logic.

-- ============================================================================
-- record_metadata — Apple Health <MetadataEntry> children of <Record>
-- ============================================================================
CREATE TABLE IF NOT EXISTS record_metadata (
    id          INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT
);
CREATE INDEX IF NOT EXISTS idx_record_metadata_message ON record_metadata(message_id);
CREATE INDEX IF NOT EXISTS idx_record_metadata_key     ON record_metadata(key);

-- ============================================================================
-- hr_samples — Apple Health <InstantaneousBeatsPerMinute> nested in Records
-- ============================================================================
CREATE TABLE IF NOT EXISTS hr_samples (
    id                  INTEGER PRIMARY KEY,
    parent_message_id   INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    ts                  TEXT NOT NULL,
    bpm                 INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hr_samples_parent ON hr_samples(parent_message_id);
CREATE INDEX IF NOT EXISTS idx_hr_samples_ts     ON hr_samples(ts);

-- ============================================================================
-- workout_events — Apple Health <WorkoutEvent> children of <Workout>
-- ============================================================================
CREATE TABLE IF NOT EXISTS workout_events (
    id                  INTEGER PRIMARY KEY,
    workout_message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    event_type          TEXT,
    date                TEXT,
    duration_seconds    REAL
);
CREATE INDEX IF NOT EXISTS idx_workout_events_workout ON workout_events(workout_message_id);

-- ============================================================================
-- workout_statistics — Apple Health <WorkoutStatistics> children of <Workout>
-- ============================================================================
CREATE TABLE IF NOT EXISTS workout_statistics (
    id                  INTEGER PRIMARY KEY,
    workout_message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    stat_type           TEXT NOT NULL,
    value_min           REAL,
    value_avg           REAL,
    value_max           REAL,
    value_sum           REAL,
    unit                TEXT,
    date_start          TEXT,
    date_end            TEXT
);
CREATE INDEX IF NOT EXISTS idx_workout_statistics_workout ON workout_statistics(workout_message_id);

-- ============================================================================
-- geo_traces — shared sidecar for trajectory points
--   Apple Health workout-routes/*.gpx GPX trkpt rows
--   Google Timeline timelinePath points
-- ============================================================================
CREATE TABLE IF NOT EXISTS geo_traces (
    id                      INTEGER PRIMARY KEY,
    parent_message_id       INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    source_kind             TEXT NOT NULL,
    point_idx               INTEGER NOT NULL,
    ts                      TEXT,
    lat                     REAL NOT NULL,
    lon                     REAL NOT NULL,
    elevation_m             REAL,
    speed_mps               REAL,
    course                  REAL,
    horizontal_accuracy_m   REAL,
    vertical_accuracy_m     REAL,
    extra_json              TEXT
);
CREATE INDEX IF NOT EXISTS idx_geo_traces_parent ON geo_traces(parent_message_id);
CREATE INDEX IF NOT EXISTS idx_geo_traces_kind   ON geo_traces(source_kind);
CREATE INDEX IF NOT EXISTS idx_geo_traces_ts     ON geo_traces(ts);

-- ============================================================================
-- Idempotent rerun: dedupe by (source_file_id, raw_hash)
-- Existing messages with NULL raw_hash are excluded by the partial predicate.
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_source_raw_hash
    ON messages(source_file_id, raw_hash)
    WHERE raw_hash IS NOT NULL AND source_file_id IS NOT NULL;

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0003_health_sidecars');
