-- Migration 0022: Drop the messages table and remove FK references to it
--
-- All 6.86M rows have been decomposed into 28 typed tables (migrations 0019-0021).
-- All cross-table relationships (threading, recipients, sidecars, chunks,
-- attachments) have been emitted as triples in the universal graph (Phase 28).
-- Adapter routing now goes directly to typed tables via _TYPED_TABLE_MAP.
--
-- Three tables (recipients, attachments, message_threads) have FK constraints
-- referencing messages(id). SQLite requires table recreation to remove FKs.
-- The message_id columns are preserved for backward compatibility.

-- Step 1: Recreate recipients without FK to messages
CREATE TABLE IF NOT EXISTS recipients_new (
    id          INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL,
    address     TEXT NOT NULL,
    name        TEXT,
    rtype       TEXT NOT NULL CHECK (rtype IN ('to', 'cc', 'bcc'))
);
INSERT INTO recipients_new SELECT * FROM recipients;
DROP TABLE IF EXISTS recipients;
ALTER TABLE recipients_new RENAME TO recipients;
CREATE INDEX IF NOT EXISTS idx_recipients_message ON recipients(message_id);
CREATE INDEX IF NOT EXISTS idx_recipients_address ON recipients(address);

-- Step 2: Recreate attachments without FK to messages
CREATE TABLE IF NOT EXISTS attachments_new (
    id                  INTEGER PRIMARY KEY,
    schema_type         TEXT NOT NULL DEFAULT 'DigitalDocument',
    message_id          INTEGER NOT NULL,
    filename            TEXT,
    content_type        TEXT,
    content_disposition TEXT,
    size_bytes          INTEGER,
    on_disk_path        TEXT,
    content_hash        TEXT
);
INSERT INTO attachments_new SELECT * FROM attachments;
DROP TABLE IF EXISTS attachments;
ALTER TABLE attachments_new RENAME TO attachments;
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_ctype ON attachments(content_type);

-- Step 3: Recreate message_threads without FK to messages
CREATE TABLE IF NOT EXISTS message_threads_new (
    message_id  INTEGER NOT NULL,
    thread_id   INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    PRIMARY KEY (message_id, thread_id)
);
INSERT INTO message_threads_new SELECT * FROM message_threads;
DROP TABLE IF EXISTS message_threads;
ALTER TABLE message_threads_new RENAME TO message_threads;
CREATE INDEX IF NOT EXISTS idx_msg_threads_thread ON message_threads(thread_id);

-- Step 4: Recreate sidecar tables without FK to messages
-- record_metadata: FK was messages(id), now observations(id)
CREATE TABLE IF NOT EXISTS record_metadata_new (
    id          INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT
);
INSERT INTO record_metadata_new SELECT * FROM record_metadata;
DROP TABLE IF EXISTS record_metadata;
ALTER TABLE record_metadata_new RENAME TO record_metadata;
CREATE INDEX IF NOT EXISTS idx_record_metadata_message ON record_metadata(message_id);
CREATE INDEX IF NOT EXISTS idx_record_metadata_key     ON record_metadata(key);

-- hr_samples: FK was messages(id), now observations(id)
CREATE TABLE IF NOT EXISTS hr_samples_new (
    id                  INTEGER PRIMARY KEY,
    parent_message_id   INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    ts                  TEXT NOT NULL,
    bpm                 INTEGER NOT NULL
);
INSERT INTO hr_samples_new SELECT * FROM hr_samples;
DROP TABLE IF EXISTS hr_samples;
ALTER TABLE hr_samples_new RENAME TO hr_samples;
CREATE INDEX IF NOT EXISTS idx_hr_samples_parent ON hr_samples(parent_message_id);
CREATE INDEX IF NOT EXISTS idx_hr_samples_ts     ON hr_samples(ts);

-- workout_events: FK was messages(id), now exercise_actions(id)
CREATE TABLE IF NOT EXISTS workout_events_new (
    id                  INTEGER PRIMARY KEY,
    workout_message_id  INTEGER NOT NULL REFERENCES exercise_actions(id) ON DELETE CASCADE,
    event_type          TEXT,
    date                TEXT,
    duration_seconds    REAL
);
INSERT INTO workout_events_new SELECT * FROM workout_events;
DROP TABLE IF EXISTS workout_events;
ALTER TABLE workout_events_new RENAME TO workout_events;
CREATE INDEX IF NOT EXISTS idx_workout_events_workout ON workout_events(workout_message_id);

-- workout_statistics: FK was messages(id), now exercise_actions(id)
CREATE TABLE IF NOT EXISTS workout_statistics_new (
    id                  INTEGER PRIMARY KEY,
    workout_message_id  INTEGER NOT NULL REFERENCES exercise_actions(id) ON DELETE CASCADE,
    stat_type           TEXT NOT NULL,
    value_min           REAL,
    value_avg           REAL,
    value_max           REAL,
    value_sum           REAL,
    unit                TEXT,
    date_start          TEXT,
    date_end            TEXT
);
INSERT INTO workout_statistics_new SELECT * FROM workout_statistics;
DROP TABLE IF EXISTS workout_statistics;
ALTER TABLE workout_statistics_new RENAME TO workout_statistics;
CREATE INDEX IF NOT EXISTS idx_workout_statistics_workout ON workout_statistics(workout_message_id);

-- geo_traces: FK was messages(id), now no single parent (used by both
-- exercise_actions and travel_actions), so remove FK constraint entirely
CREATE TABLE IF NOT EXISTS geo_traces_new (
    id                      INTEGER PRIMARY KEY,
    parent_message_id       INTEGER,
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
INSERT INTO geo_traces_new SELECT * FROM geo_traces;
DROP TABLE IF EXISTS geo_traces;
ALTER TABLE geo_traces_new RENAME TO geo_traces;
CREATE INDEX IF NOT EXISTS idx_geo_traces_parent ON geo_traces(parent_message_id);
CREATE INDEX IF NOT EXISTS idx_geo_traces_kind   ON geo_traces(source_kind);
CREATE INDEX IF NOT EXISTS idx_geo_traces_ts     ON geo_traces(ts);

-- Step 5: Drop the messages table
DROP TABLE IF EXISTS messages;

-- Step 6: Repoint chunks.source_table from 'messages' to actual typed tables.
-- IDs are globally unique (preserved from messages during migration).
UPDATE chunks SET source_table = 'emails'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM emails);
UPDATE chunks SET source_table = 'chat_messages'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM chat_messages);
UPDATE chunks SET source_table = 'conversations_messages'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM conversations_messages);
UPDATE chunks SET source_table = 'observations'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM observations);
UPDATE chunks SET source_table = 'exercise_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM exercise_actions);
UPDATE chunks SET source_table = 'search_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM search_actions);
UPDATE chunks SET source_table = 'listen_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM listen_actions);
UPDATE chunks SET source_table = 'watch_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM watch_actions);
UPDATE chunks SET source_table = 'actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM actions);
UPDATE chunks SET source_table = 'events'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM events);
UPDATE chunks SET source_table = 'products'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM products);
UPDATE chunks SET source_table = 'order_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM order_actions);
UPDATE chunks SET source_table = 'like_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM like_actions);
UPDATE chunks SET source_table = 'persons'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM persons);
UPDATE chunks SET source_table = 'social_postings'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM social_postings);
UPDATE chunks SET source_table = 'comments'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM comments);
UPDATE chunks SET source_table = 'places'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM places);
UPDATE chunks SET source_table = 'travel_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM travel_actions);
UPDATE chunks SET source_table = 'geo_shapes'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM geo_shapes);
UPDATE chunks SET source_table = 'books'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM books);
UPDATE chunks SET source_table = 'medical_records'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM medical_records);
UPDATE chunks SET source_table = 'reviews'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM reviews);
UPDATE chunks SET source_table = 'invite_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM invite_actions);
UPDATE chunks SET source_table = 'creative_works'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM creative_works);
UPDATE chunks SET source_table = 'web_pages'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM web_pages);
UPDATE chunks SET source_table = 'join_actions'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM join_actions);
UPDATE chunks SET source_table = 'digital_documents'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM digital_documents);
UPDATE chunks SET source_table = 'things'
  WHERE source_table = 'messages' AND source_id IN (SELECT id FROM things);

-- Step 7: Drop legacy relationship tables — data lives in triples now
DROP TABLE IF EXISTS message_threads;
DROP TABLE IF EXISTS recipients;
DROP TABLE IF EXISTS threads;

INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0022_drop_messages');
