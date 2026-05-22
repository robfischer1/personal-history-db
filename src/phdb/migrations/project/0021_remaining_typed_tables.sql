-- Migration 0021 — Create remaining typed tables from messages decomposition
-- Created: 2026-05-22
--
-- Phases 3-9 + 10-27 of Messages Decomposition. Creates typed tables for all
-- remaining schema_types in messages, migrates data (preserving IDs), and
-- repoints chunks. Each type gets its own table regardless of row count.
--
-- Post-migration: DELETE FROM messages handled separately after validation.
--
-- Batch approach: one migration creates all tables + migrates all data.
-- This is safe because all source data is in messages and the types are disjoint.

-----------------------------------------------------------------------
-- Phase 3: SearchAction (124K rows)
-----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS search_actions (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'SearchAction',
    action_key        TEXT,
    subject           TEXT,
    source_device     TEXT,
    sender_name       TEXT,
    direction         TEXT NOT NULL DEFAULT 'self',
    date_performed    TEXT,
    body_text         TEXT,
    body_text_source  TEXT,
    body_text_hash    TEXT,
    is_bulk           INTEGER NOT NULL DEFAULT 1,
    bulk_signal       TEXT,
    source_byte_offset INTEGER,
    source_byte_length INTEGER,
    raw_hash          TEXT,
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_search_actions_dedup ON search_actions(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_search_actions_date ON search_actions(date_performed);

INSERT INTO search_actions (
    id, schema_type, action_key, subject, source_device, sender_name,
    direction, date_performed, body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, created_at
) SELECT
    id, schema_type, rfc822_message_id, subject, sender_address, sender_name,
    direction, date_sent, body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, ingested_at
FROM messages WHERE schema_type = 'SearchAction';

-----------------------------------------------------------------------
-- Phase 4: EmailMessage (68K rows)
-----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emails (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'EmailMessage',
    rfc822_message_id TEXT,
    in_reply_to       TEXT,
    references_chain  TEXT,
    gmail_thread_id   TEXT,
    gmail_labels      TEXT,
    subject           TEXT,
    sender_address    TEXT,
    sender_name       TEXT,
    sender_domain     TEXT,
    direction         TEXT NOT NULL DEFAULT 'unknown',
    date_sent         TEXT,
    date_received     TEXT,
    body_text         TEXT,
    body_text_source  TEXT,
    body_text_hash    TEXT,
    is_multipart      INTEGER NOT NULL DEFAULT 0,
    has_attachments   INTEGER NOT NULL DEFAULT 0,
    attachment_count  INTEGER NOT NULL DEFAULT 0,
    is_bulk           INTEGER NOT NULL DEFAULT 0,
    bulk_signal       TEXT,
    source_byte_offset INTEGER,
    source_byte_length INTEGER,
    raw_hash          TEXT,
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_dedup ON emails(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_sent);
CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender_address);
CREATE INDEX IF NOT EXISTS idx_emails_thread ON emails(gmail_thread_id);
CREATE INDEX IF NOT EXISTS idx_emails_rfc822 ON emails(rfc822_message_id) WHERE rfc822_message_id IS NOT NULL;

INSERT INTO emails (
    id, schema_type, rfc822_message_id, in_reply_to, references_chain,
    gmail_thread_id, gmail_labels, subject, sender_address, sender_name,
    sender_domain, direction, date_sent, date_received,
    body_text, body_text_source, body_text_hash,
    is_multipart, has_attachments, attachment_count,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, created_at
) SELECT
    id, schema_type, rfc822_message_id, in_reply_to, references_chain,
    gmail_thread_id, gmail_labels, subject, sender_address, sender_name,
    sender_domain, direction, date_sent, date_received,
    body_text, body_text_source, body_text_hash,
    is_multipart, has_attachments, attachment_count,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, ingested_at
FROM messages WHERE schema_type = 'EmailMessage';

-----------------------------------------------------------------------
-- Phase 5: Conversation (62K rows)
-----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations_messages (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'Conversation',
    conversation_key  TEXT,
    subject           TEXT,
    sender_address    TEXT,
    sender_name       TEXT,
    sender_domain     TEXT,
    direction         TEXT NOT NULL DEFAULT 'unknown',
    date_sent         TEXT,
    body_text         TEXT,
    body_text_source  TEXT,
    body_text_hash    TEXT,
    is_bulk           INTEGER NOT NULL DEFAULT 0,
    bulk_signal       TEXT,
    kind              TEXT,
    role              TEXT,
    parent_uuid       TEXT,
    tool_name         TEXT,
    tool_use_id       TEXT,
    model             TEXT,
    payload           TEXT,
    raw_hash          TEXT,
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_messages_dedup ON conversations_messages(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_conversations_messages_date ON conversations_messages(date_sent);
CREATE INDEX IF NOT EXISTS idx_conversations_messages_kind ON conversations_messages(kind);
CREATE INDEX IF NOT EXISTS idx_conversations_messages_model ON conversations_messages(model) WHERE model IS NOT NULL;

INSERT INTO conversations_messages (
    id, schema_type, conversation_key, subject, sender_address, sender_name,
    sender_domain, direction, date_sent,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal,
    kind, role, parent_uuid, tool_name, tool_use_id, model, payload,
    raw_hash, source_file_id, created_at
) SELECT
    id, schema_type, rfc822_message_id, subject, sender_address, sender_name,
    sender_domain, direction, date_sent,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal,
    kind, role, parent_uuid, tool_name, tool_use_id, model, payload,
    raw_hash, source_file_id, ingested_at
FROM messages WHERE schema_type = 'Conversation';

-----------------------------------------------------------------------
-- Phase 6: ExerciseAction (45K rows)
-----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exercise_actions (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'ExerciseAction',
    exercise_key      TEXT,
    type_identifier   TEXT,
    subject           TEXT,
    source_device     TEXT,
    sender_domain     TEXT,
    direction         TEXT NOT NULL DEFAULT 'self',
    date_performed    TEXT,
    date_end          TEXT,
    body_text         TEXT,
    body_text_source  TEXT,
    body_text_hash    TEXT,
    is_bulk           INTEGER NOT NULL DEFAULT 1,
    bulk_signal       TEXT,
    source_byte_offset INTEGER,
    source_byte_length INTEGER,
    raw_hash          TEXT,
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exercise_actions_dedup ON exercise_actions(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_exercise_actions_date ON exercise_actions(date_performed);
CREATE INDEX IF NOT EXISTS idx_exercise_actions_type ON exercise_actions(type_identifier);

INSERT INTO exercise_actions (
    id, schema_type, exercise_key, type_identifier, subject, source_device,
    sender_domain, direction, date_performed, date_end,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, created_at
) SELECT
    id, schema_type, rfc822_message_id, sender_name, subject, sender_address,
    sender_domain, direction, date_sent, date_received,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, ingested_at
FROM messages WHERE schema_type = 'ExerciseAction';

-----------------------------------------------------------------------
-- Phase 7: ListenAction (44K rows)
-----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS listen_actions (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'ListenAction',
    listen_key        TEXT,
    subject           TEXT,
    artist_name       TEXT,
    source_device     TEXT,
    direction         TEXT NOT NULL DEFAULT 'self',
    date_listened     TEXT,
    body_text         TEXT,
    body_text_source  TEXT,
    body_text_hash    TEXT,
    is_bulk           INTEGER NOT NULL DEFAULT 1,
    bulk_signal       TEXT,
    source_byte_offset INTEGER,
    source_byte_length INTEGER,
    raw_hash          TEXT,
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_listen_actions_dedup ON listen_actions(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_listen_actions_date ON listen_actions(date_listened);

INSERT INTO listen_actions (
    id, schema_type, listen_key, subject, artist_name, source_device,
    direction, date_listened,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, created_at
) SELECT
    id, schema_type, rfc822_message_id, subject, sender_name, sender_address,
    direction, date_sent,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, ingested_at
FROM messages WHERE schema_type = 'ListenAction';

-----------------------------------------------------------------------
-- Phase 8: WatchAction (35K rows)
-----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watch_actions (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'WatchAction',
    watch_key         TEXT,
    subject           TEXT,
    platform_name     TEXT,
    source_device     TEXT,
    direction         TEXT NOT NULL DEFAULT 'self',
    date_watched      TEXT,
    body_text         TEXT,
    body_text_source  TEXT,
    body_text_hash    TEXT,
    is_bulk           INTEGER NOT NULL DEFAULT 1,
    bulk_signal       TEXT,
    source_byte_offset INTEGER,
    source_byte_length INTEGER,
    raw_hash          TEXT,
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_actions_dedup ON watch_actions(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_watch_actions_date ON watch_actions(date_watched);

INSERT INTO watch_actions (
    id, schema_type, watch_key, subject, platform_name, source_device,
    direction, date_watched,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, created_at
) SELECT
    id, schema_type, rfc822_message_id, subject, sender_name, sender_address,
    direction, date_sent,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, ingested_at
FROM messages WHERE schema_type = 'WatchAction';

-----------------------------------------------------------------------
-- Phase 9: Action (29K rows) — generic catch-all
-----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS actions (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'Action',
    action_key        TEXT,
    subject           TEXT,
    sender_address    TEXT,
    sender_name       TEXT,
    direction         TEXT NOT NULL DEFAULT 'unknown',
    date_performed    TEXT,
    date_received     TEXT,
    body_text         TEXT,
    body_text_source  TEXT,
    body_text_hash    TEXT,
    is_bulk           INTEGER NOT NULL DEFAULT 0,
    bulk_signal       TEXT,
    source_byte_offset INTEGER,
    source_byte_length INTEGER,
    raw_hash          TEXT,
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_dedup ON actions(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_actions_date ON actions(date_performed);

INSERT INTO actions (
    id, schema_type, action_key, subject, sender_address, sender_name,
    direction, date_performed, date_received,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, created_at
) SELECT
    id, schema_type, rfc822_message_id, subject, sender_address, sender_name,
    direction, date_sent, date_received,
    body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, ingested_at
FROM messages WHERE schema_type = 'Action';

-----------------------------------------------------------------------
-- Phase 10: Photograph (262 rows) — migrate to existing photographs table
-----------------------------------------------------------------------
INSERT OR IGNORE INTO photographs (
    schema_type, source_path, album_root, content_hash,
    captured_at, width, height,
    source_org, source_kind, provenance,
    raw_hash, source_file_id
)
SELECT
    'Photograph',
    COALESCE(subject, 'unknown'),
    'phone-camera',
    body_text_hash,
    date_sent,
    NULL, NULL,
    'phone-camera', 'photo-metadata',
    'phone-camera:messages-migration',
    raw_hash, source_file_id
FROM messages WHERE schema_type = 'Photograph';

-----------------------------------------------------------------------
-- Phases 11-27: Remaining small types — all use a generic shape
-----------------------------------------------------------------------

-- Phase 11: Event (7,290 rows)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'Event',
    event_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'unknown', date_occurred TEXT, date_received TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    source_byte_offset INTEGER, source_byte_length INTEGER,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup ON events(source_file_id, raw_hash);
INSERT INTO events (id, schema_type, event_key, subject, sender_address, sender_name, direction, date_occurred, date_received, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, date_received, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'Event';

-- Phase 12: Product (6,513 rows)
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'Product',
    product_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_recorded TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    source_byte_offset INTEGER, source_byte_length INTEGER,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_dedup ON products(source_file_id, raw_hash);
INSERT INTO products (id, schema_type, product_key, subject, sender_address, sender_name, direction, date_recorded, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'Product';

-- Phase 13: OrderAction (2,141 rows)
CREATE TABLE IF NOT EXISTS order_actions (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'OrderAction',
    order_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_ordered TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    source_byte_offset INTEGER, source_byte_length INTEGER,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_order_actions_dedup ON order_actions(source_file_id, raw_hash);
INSERT INTO order_actions (id, schema_type, order_key, subject, sender_address, sender_name, direction, date_ordered, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'OrderAction';

-- Phase 14: LikeAction (1,483 rows)
CREATE TABLE IF NOT EXISTS like_actions (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'LikeAction',
    like_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_liked TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    source_byte_offset INTEGER, source_byte_length INTEGER,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_like_actions_dedup ON like_actions(source_file_id, raw_hash);
INSERT INTO like_actions (id, schema_type, like_key, subject, sender_address, sender_name, direction, date_liked, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'LikeAction';

-- Phase 15: Person (1,431 rows)
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'Person',
    person_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_recorded TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    source_byte_offset INTEGER, source_byte_length INTEGER,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_persons_dedup ON persons(source_file_id, raw_hash);
INSERT INTO persons (id, schema_type, person_key, subject, sender_address, sender_name, direction, date_recorded, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'Person';

-- Phase 16: SocialMediaPosting (595 rows)
CREATE TABLE IF NOT EXISTS social_postings (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'SocialMediaPosting',
    posting_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT, sender_domain TEXT,
    direction TEXT NOT NULL DEFAULT 'outbound', date_posted TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    source_byte_offset INTEGER, source_byte_length INTEGER,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_postings_dedup ON social_postings(source_file_id, raw_hash);
INSERT INTO social_postings (id, schema_type, posting_key, subject, sender_address, sender_name, sender_domain, direction, date_posted, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, sender_domain, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'SocialMediaPosting';

-- Phase 17: Comment (430 rows)
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'Comment',
    comment_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'outbound', date_posted TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_comments_dedup ON comments(source_file_id, raw_hash);
INSERT INTO comments (id, schema_type, comment_key, subject, sender_address, sender_name, direction, date_posted, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'Comment';

-- Phase 18: Place (399 rows)
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'Place',
    place_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_recorded TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    source_byte_offset INTEGER, source_byte_length INTEGER,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_places_dedup ON places(source_file_id, raw_hash);
INSERT INTO places (id, schema_type, place_key, subject, sender_address, sender_name, direction, date_recorded, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'Place';

-- Phase 19: TravelAction (354 rows)
CREATE TABLE IF NOT EXISTS travel_actions (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'TravelAction',
    travel_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_traveled TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    source_byte_offset INTEGER, source_byte_length INTEGER,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_travel_actions_dedup ON travel_actions(source_file_id, raw_hash);
INSERT INTO travel_actions (id, schema_type, travel_key, subject, sender_address, sender_name, direction, date_traveled, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, source_byte_offset, source_byte_length, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'TravelAction';

-- Phase 20: GeoShape (347 rows)
CREATE TABLE IF NOT EXISTS geo_shapes (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'GeoShape',
    geo_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_recorded TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_geo_shapes_dedup ON geo_shapes(source_file_id, raw_hash);
INSERT INTO geo_shapes (id, schema_type, geo_key, subject, sender_address, sender_name, direction, date_recorded, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'GeoShape';

-- Phase 21: Book (249 rows)
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'Book',
    book_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_recorded TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_books_dedup ON books(source_file_id, raw_hash);
INSERT INTO books (id, schema_type, book_key, subject, sender_address, sender_name, direction, date_recorded, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'Book';

-- Phase 22: MedicalRecord (198 rows)
CREATE TABLE IF NOT EXISTS medical_records (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'MedicalRecord',
    record_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_recorded TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_medical_records_dedup ON medical_records(source_file_id, raw_hash);
INSERT INTO medical_records (id, schema_type, record_key, subject, sender_address, sender_name, direction, date_recorded, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'MedicalRecord';

-- Phase 23: Review (140 rows)
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'Review',
    review_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_reviewed TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_dedup ON reviews(source_file_id, raw_hash);
INSERT INTO reviews (id, schema_type, review_key, subject, sender_address, sender_name, direction, date_reviewed, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'Review';

-- Phase 24: InviteAction (51 rows)
CREATE TABLE IF NOT EXISTS invite_actions (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'InviteAction',
    invite_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'unknown', date_invited TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_invite_actions_dedup ON invite_actions(source_file_id, raw_hash);
INSERT INTO invite_actions (id, schema_type, invite_key, subject, sender_address, sender_name, direction, date_invited, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'InviteAction';

-- Phase 25: CreativeWork (37 rows)
CREATE TABLE IF NOT EXISTS creative_works (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'CreativeWork',
    work_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_created TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_creative_works_dedup ON creative_works(source_file_id, raw_hash);
INSERT INTO creative_works (id, schema_type, work_key, subject, sender_address, sender_name, direction, date_created, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'CreativeWork';

-- Phase 26: WebPage (30 rows)
CREATE TABLE IF NOT EXISTS web_pages (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'WebPage',
    page_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_recorded TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_web_pages_dedup ON web_pages(source_file_id, raw_hash);
INSERT INTO web_pages (id, schema_type, page_key, subject, sender_address, sender_name, direction, date_recorded, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'WebPage';

-- Phase 27: JoinAction (8) + DigitalDocument (5) + Thing (1)
CREATE TABLE IF NOT EXISTS join_actions (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'JoinAction',
    join_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_joined TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_join_actions_dedup ON join_actions(source_file_id, raw_hash);
INSERT INTO join_actions (id, schema_type, join_key, subject, sender_address, sender_name, direction, date_joined, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'JoinAction';

CREATE TABLE IF NOT EXISTS digital_documents (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'DigitalDocument',
    doc_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_created TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_digital_documents_dedup ON digital_documents(source_file_id, raw_hash);
INSERT INTO digital_documents (id, schema_type, doc_key, subject, sender_address, sender_name, direction, date_created, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'DigitalDocument';

CREATE TABLE IF NOT EXISTS things (
    id INTEGER PRIMARY KEY, schema_type TEXT NOT NULL DEFAULT 'Thing',
    thing_key TEXT, subject TEXT, sender_address TEXT, sender_name TEXT,
    direction TEXT NOT NULL DEFAULT 'self', date_recorded TEXT,
    body_text TEXT, body_text_source TEXT, body_text_hash TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0, bulk_signal TEXT,
    raw_hash TEXT, source_file_id INTEGER REFERENCES source_files(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_things_dedup ON things(source_file_id, raw_hash);
INSERT INTO things (id, schema_type, thing_key, subject, sender_address, sender_name, direction, date_recorded, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, created_at)
SELECT id, schema_type, rfc822_message_id, subject, sender_address, sender_name, direction, date_sent, body_text, body_text_source, body_text_hash, is_bulk, bulk_signal, raw_hash, source_file_id, ingested_at FROM messages WHERE schema_type = 'Thing';

-----------------------------------------------------------------------
-- Repoint all chunks for migrated types
-----------------------------------------------------------------------
UPDATE chunks SET source_table = 'search_actions' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM search_actions);
UPDATE chunks SET source_table = 'emails' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM emails);
UPDATE chunks SET source_table = 'conversations_messages' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM conversations_messages);
UPDATE chunks SET source_table = 'exercise_actions' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM exercise_actions);
UPDATE chunks SET source_table = 'listen_actions' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM listen_actions);
UPDATE chunks SET source_table = 'watch_actions' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM watch_actions);
UPDATE chunks SET source_table = 'actions' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM actions);
UPDATE chunks SET source_table = 'events' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM events);
UPDATE chunks SET source_table = 'products' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM products);
UPDATE chunks SET source_table = 'social_postings' WHERE source_table = 'messages' AND source_id IN (SELECT id FROM social_postings);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0021_remaining_typed_tables');
