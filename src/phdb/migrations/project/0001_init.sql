-- Personal History DB — initial schema
-- Created: 2026-05-02
-- Engine: SQLite 3.37+ with sqlite-vec extension (vec0) and FTS5
--
-- Architecture: vault is canonical source-of-truth for narrative; this DB is
-- a recompute-only structured + vector sidecar for tabular and semantic queries.
-- Per AGENTS.md / SCHEMA.md, every row carries its Schema.org @type so rows are
-- export-ready as JSON-LD if ever needed.
--
-- Migrations are tracked in `schema_migrations` and applied in numeric order.

-- ============================================================================
-- Migration tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id    TEXT PRIMARY KEY,
    applied_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ============================================================================
-- Source-file provenance
-- ============================================================================
CREATE TABLE IF NOT EXISTS source_files (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'Dataset',  -- Schema.org @type
    source_path       TEXT NOT NULL,                     -- e.g. "D:\<archives>\...mbox"
    source_org        TEXT,                              -- e.g. "Google Takeout"
    file_kind         TEXT,                              -- 'mbox', 'sqlite', 'csv', etc.
    file_size         INTEGER,
    file_hash         TEXT,                              -- sha256 of file
    message_count     INTEGER,                           -- rows derived from this source
    ingested_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    notes             TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_files_path ON source_files(source_path);

-- ============================================================================
-- Messages — one row per email, dedupe by RFC822 Message-ID
-- ============================================================================
CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY,
    schema_type         TEXT NOT NULL DEFAULT 'EmailMessage',  -- Schema.org @type
    rfc822_message_id   TEXT,                                  -- nullable but indexed-uniqueish
    in_reply_to         TEXT,
    references_chain    TEXT,                                  -- raw References header

    -- Gmail-specific (from .mbox export headers)
    gmail_thread_id     TEXT,                                  -- X-GM-THRID
    gmail_labels        TEXT,                                  -- JSON array of strings

    -- Headers
    subject             TEXT,
    sender_address      TEXT,                                  -- normalized lowercase
    sender_name         TEXT,
    sender_domain       TEXT,                                  -- denormalized for fast filtering

    -- Direction (owner is sender or recipient)
    direction           TEXT CHECK (direction IN ('inbound', 'outbound', 'self', 'unknown')),

    -- Timestamps (ISO 8601)
    date_sent           TEXT,                                  -- from Date header
    date_received       TEXT,                                  -- from last Received header

    -- Body
    body_text           TEXT,                                  -- preferred plain-text body
    body_html           TEXT,                                  -- raw HTML if present
    body_text_source    TEXT,                                  -- 'plain', 'html2text', 'snippet', 'empty'

    -- Flags
    is_multipart        INTEGER NOT NULL DEFAULT 0,
    has_attachments     INTEGER NOT NULL DEFAULT 0,
    attachment_count    INTEGER NOT NULL DEFAULT 0,
    is_bulk             INTEGER NOT NULL DEFAULT 0,            -- List-Unsubscribe / no-reply / etc.
    bulk_signal         TEXT,                                  -- which heuristic flagged it

    -- Provenance
    source_file_id      INTEGER REFERENCES source_files(id),
    source_byte_offset  INTEGER,                               -- start offset in mbox
    source_byte_length  INTEGER,                               -- length of raw message
    raw_hash            TEXT,                                  -- sha256 of raw bytes
    body_text_hash      TEXT,                                  -- sha256 of body_text

    -- Lifecycle
    ingested_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_rfc_msgid       ON messages(rfc822_message_id) WHERE rfc822_message_id IS NOT NULL;
CREATE INDEX        IF NOT EXISTS idx_messages_date_sent       ON messages(date_sent);
CREATE INDEX        IF NOT EXISTS idx_messages_sender_address  ON messages(sender_address);
CREATE INDEX        IF NOT EXISTS idx_messages_sender_domain   ON messages(sender_domain);
CREATE INDEX        IF NOT EXISTS idx_messages_gmail_thread    ON messages(gmail_thread_id);
CREATE INDEX        IF NOT EXISTS idx_messages_is_bulk         ON messages(is_bulk);
CREATE INDEX        IF NOT EXISTS idx_messages_direction       ON messages(direction);

-- ============================================================================
-- Recipients — normalized one-row-per-(message, recipient)
-- ============================================================================
CREATE TABLE IF NOT EXISTS recipients (
    id          INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    address     TEXT NOT NULL,                       -- normalized lowercase
    name        TEXT,
    rtype       TEXT NOT NULL CHECK (rtype IN ('to', 'cc', 'bcc'))
);
CREATE INDEX IF NOT EXISTS idx_recipients_message ON recipients(message_id);
CREATE INDEX IF NOT EXISTS idx_recipients_address ON recipients(address);

-- ============================================================================
-- Attachments — metadata only (no extracted file content)
-- ============================================================================
CREATE TABLE IF NOT EXISTS attachments (
    id                  INTEGER PRIMARY KEY,
    schema_type         TEXT NOT NULL DEFAULT 'DigitalDocument',
    message_id          INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename            TEXT,
    content_type        TEXT,
    content_disposition TEXT,
    size_bytes          INTEGER,
    on_disk_path        TEXT,                                  -- NULL = not extracted
    content_hash        TEXT
);
CREATE INDEX IF NOT EXISTS idx_attachments_message  ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_ctype    ON attachments(content_type);

-- ============================================================================
-- Threads — derived view of conversation groupings
-- Populated post-ingest from messages.gmail_thread_id (preferred)
-- with fallback to In-Reply-To/References chains.
-- ============================================================================
CREATE TABLE IF NOT EXISTS threads (
    id                  INTEGER PRIMARY KEY,
    schema_type         TEXT NOT NULL DEFAULT 'Conversation',
    gmail_thread_id     TEXT,                                  -- if known
    subject_canonical   TEXT,                                  -- subject of earliest message, Re:/Fwd: stripped
    message_count       INTEGER NOT NULL DEFAULT 0,
    date_first          TEXT,
    date_last           TEXT,
    participants        TEXT                                   -- JSON array of normalized addresses
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_gmail_id  ON threads(gmail_thread_id) WHERE gmail_thread_id IS NOT NULL;
CREATE INDEX        IF NOT EXISTS idx_threads_date_last ON threads(date_last);

-- Bridge messages → threads
CREATE TABLE IF NOT EXISTS message_threads (
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    thread_id   INTEGER NOT NULL REFERENCES threads(id)  ON DELETE CASCADE,
    PRIMARY KEY (message_id, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_msg_threads_thread ON message_threads(thread_id);

-- ============================================================================
-- People resolution — link email addresses to vault Entities/People notes
-- ============================================================================
CREATE TABLE IF NOT EXISTS people_resolution (
    id                  INTEGER PRIMARY KEY,
    address             TEXT NOT NULL UNIQUE,                  -- normalized lowercase email
    person_note_path    TEXT,                                  -- e.g. "Entities/People/Jane Doe.md"
    confidence          REAL,                                  -- 0.0-1.0
    resolved_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resolution_method   TEXT                                   -- 'exact_email', 'manual', 'fuzzy_name', etc.
);

-- ============================================================================
-- Generic document/embedding registry
-- Holds chunked content from ANY source (messages, people entities,
-- inventories, future Calibre/Raindrop/etc.) for unified semantic search.
-- ============================================================================
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL,                             -- Schema.org @type of the source row
    source_table    TEXT NOT NULL,                             -- 'messages', 'people', 'inventory_md', etc.
    source_id       INTEGER NOT NULL,                          -- FK to source row (or path-hash for vault md)
    chunk_index     INTEGER NOT NULL DEFAULT 0,                -- 0..N within source row
    chunk_strategy  TEXT,                                      -- 'message_body_512tok', 'people_md', etc.

    title           TEXT,                                      -- denormalized for display
    content         TEXT NOT NULL,                             -- the chunk text (also the FTS source)
    content_hash    TEXT,                                      -- sha256 of content; for dedupe
    metadata_json   TEXT,                                      -- arbitrary structured per-source metadata

    embedding_model TEXT,                                      -- e.g. 'nomic-embed-text-v1.5-Q'
    embedded_at     TEXT,                                      -- NULL until embedded
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX        IF NOT EXISTS idx_documents_source        ON documents(source_table, source_id);
CREATE INDEX        IF NOT EXISTS idx_documents_schema_type   ON documents(schema_type);
CREATE INDEX        IF NOT EXISTS idx_documents_embedded_at   ON documents(embedded_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_src_chunk     ON documents(source_table, source_id, chunk_index);

-- ============================================================================
-- Vector index — sqlite-vec vec0 virtual table
-- rowid here MUST equal documents.id for FK joins
-- ============================================================================
-- (Created at runtime by ingest script after sqlite-vec extension is loaded.)
-- DDL recorded here for reference; can't be in this file because vec0 requires
-- the extension loaded BEFORE the CREATE statement is parsed.
--
-- CREATE VIRTUAL TABLE IF NOT EXISTS doc_vectors USING vec0(
--     embedding float[768]
-- );

-- ============================================================================
-- FTS5 full-text index — external-content mode pointing at documents
-- ============================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    content,
    title,
    content='documents',
    content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);

-- Triggers to keep FTS in sync with documents
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO doc_fts(rowid, content, title) VALUES (new.id, new.content, new.title);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO doc_fts(doc_fts, rowid, content, title) VALUES ('delete', old.id, old.content, old.title);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE OF content, title ON documents BEGIN
    INSERT INTO doc_fts(doc_fts, rowid, content, title) VALUES ('delete', old.id, old.content, old.title);
    INSERT INTO doc_fts(rowid, content, title)         VALUES (new.id, new.content, new.title);
END;

-- ============================================================================
-- Done
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0001_init');
