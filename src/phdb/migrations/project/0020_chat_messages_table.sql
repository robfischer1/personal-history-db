-- Migration 0020 — Create chat_messages typed table + migrate Message rows
-- Created: 2026-05-22
--
-- Phase 2 of Messages Decomposition: 462K Message rows (chat/SMS/Discord/etc.)
-- Heaviest recipient and threading usage of any type.
--
-- Post-migration steps (NOT in this file):
--   1. Convert message_threads to inThread/threadContains triples
--   2. Convert recipients to sentTo/receivedFrom triples
--   3. DELETE FROM messages WHERE schema_type = 'Message'
--   4. Cleanup message_threads/recipients for migrated rows
--
-- Source adapters: chat-logs, imessage, discord, sms-xml, phone-sms,
--   gmail-sms-backfill, google-voice, facebook, titaniumbackup-twitter, staged-md

CREATE TABLE IF NOT EXISTS chat_messages (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'Message',
    message_key       TEXT,                  -- dedup key (was rfc822_message_id)
    subject           TEXT,                  -- rare for chat; voicemail subjects
    sender_address    TEXT,                  -- phone number or platform handle
    sender_name       TEXT,
    sender_domain     TEXT,                  -- rare; some spam domains
    direction         TEXT NOT NULL DEFAULT 'unknown',
    date_sent         TEXT,                  -- ISO 8601
    date_received     TEXT,                  -- ISO 8601 (sparse for chat)
    body_text         TEXT,
    body_text_source  TEXT,                  -- format indicator
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_messages_dedup
    ON chat_messages(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_chat_messages_date
    ON chat_messages(date_sent) WHERE date_sent IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_messages_sender
    ON chat_messages(sender_address) WHERE sender_address IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_messages_direction
    ON chat_messages(direction);

-- Migrate data: preserve message IDs
INSERT INTO chat_messages (
    id, schema_type, message_key, subject,
    sender_address, sender_name, sender_domain,
    direction, date_sent, date_received,
    body_text, body_text_source, body_text_hash,
    is_multipart, has_attachments, attachment_count,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, created_at
)
SELECT
    id, schema_type, rfc822_message_id, subject,
    sender_address, sender_name, sender_domain,
    direction, date_sent, date_received,
    body_text, body_text_source, body_text_hash,
    is_multipart, has_attachments, attachment_count,
    is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    raw_hash, source_file_id, ingested_at
FROM messages
WHERE schema_type = 'Message';

-- Repoint chunks
UPDATE chunks SET source_table = 'chat_messages'
WHERE source_table = 'messages'
  AND source_id IN (SELECT id FROM chat_messages);

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0020_chat_messages_table');
