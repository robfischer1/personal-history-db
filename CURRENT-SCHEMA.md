---
created: 2026-05-06
updated: 2026-05-27
status: superseded
type: project-reference
related:
  - "[[REWRITE_PLAN]]"
  - "[[INVENTORY]]"
  - "[[project_personal_history_db]]"
---

# Personal-History-DB — Current Schema Snapshot

> **Superseded.** This manual snapshot covers migrations 0001–0009 only. The live DB is at migration 0042 with 100+ tables and 52 declared schemas. Use `phdb schema regenerate` to produce the current `DB_SCHEMA.md`, which includes entity tables, action/document tables, infrastructure tables, facet plugins, and migration history — all from the live DB.

Static reference of the SQL schema as defined by migrations 0001–0009 in `src/phdb/migrations/project/`. Documents the table definitions, indexes, triggers, and constraints that the framework produces.

## Migration status

| Migration | File | Applied | Notes |
|---|---|---|---|
| 0001_init | `0001_init.sql` | ✓ | Core message schema + chunk registry/FTS |
| 0002_conversation_generalization | `0002_conversation_generalization.sql` | ✓ | Adds `source_kind` + `thread_key` for non-Gmail threads |
| 0003_health_sidecars | `0003_health_sidecars.sql` | ✓ | Apple Health + Google Timeline sidecars; idempotency index |
| 0004_bookmarks | `0004_bookmarks.sql` | ✓ | Raindrop + browser bookmarks |
| 0005_connections | `0005_connections.sql` | ✓ | Facebook social connections |
| 0006_ai_sessions | `0006_ai_sessions.sql` | ✓ | AI session-specific columns (`kind`, `session_source`, `ai_model`, etc.) |
| 0007_chunks_rename | `0007_chunks_rename.sql` | ✓ | Renames `documents` → `chunks` (chunk registry); recreates FTS + triggers under new names |
| 0008_documents_typed_table | `0008_documents_typed_table.sql` | ✓ | Creates `documents` typed table for DigitalDocument rows |
| 0009_documents_migrate | `0009_documents_migrate.sql` | ✓ | Moves DigitalDocument rows from `messages` → `documents`; repoints chunks; cleans orphan threads |

Migrations 0001–0006 applied to the production DB as of 2026-05-08. Migrations 0007–0009 pending application (typed-tables reshape).

---

## Architectural conventions

- **Vault is canonical** for narrative; this DB is a recompute-only structured + vector sidecar.
- **Every row carries Schema.org `@type`** in a `schema_type` column — rows are JSON-LD-export-ready.
- **Timestamps are ISO-8601 strings** — `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` is the canonical `now()`.
- **Idempotent ingestion** is enforced by partial unique indexes on `messages(source_file_id, raw_hash)` and `documents(source_file_id, raw_hash)`.
- **Typed tables** are the pattern for non-message domains: `documents` for DigitalDocument (keyed on `source_file_id, raw_hash`), `bookmarks` for BookmarkAction (keyed on `normalized_url, instrument`), `connections` for BefriendAction (keyed on `dedupe_key, instrument`). Future @type domains follow the same shape.
- **Provenance always traceable** — `source_files` row + `raw_hash` per ingested row.
- **Soft delete via `excluded` columns** rather than physical delete, where applicable.

---

## Core tables

### `schema_migrations`
Tracks applied migrations. Single column `migration_id` (TEXT PK) plus `applied_at`.

### `source_files`
Provenance for every ingested source. One row per source file.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `schema_type` | TEXT | Default `'Dataset'` |
| `source_path` | TEXT NOT NULL | UNIQUE indexed |
| `source_org` | TEXT | e.g., 'Google Takeout' |
| `file_kind` | TEXT | Format: `mbox`, `sqlite`, `csv`, `html`, `json`, `xml` |
| `source_kind` | TEXT | Origin: `gmail`, `imessage`, `msn`, `aim`, `yahoo`, etc. (added in 002) |
| `file_size` | INTEGER | |
| `file_hash` | TEXT | sha256 of source file |
| `message_count` | INTEGER | rows derived from this source |
| `ingested_at` | TEXT NOT NULL | ISO timestamp |
| `notes` | TEXT | |

**Indexes:** `idx_source_files_path` (UNIQUE), `idx_source_files_source_kind`.

**Key insight:** `file_kind` (format) and `source_kind` (origin) are deliberately separate dimensions. A `mbox` file from Gmail and a `mbox` file from Yahoo share `file_kind` but differ in `source_kind`.

---

### `messages`
The core fact table. One row per atomic message unit (email, SMS, iMessage, Discord message, Apple Health record, workout, calendar event, etc.). The `schema_type` column distinguishes them.

**Key columns** (full list in `001_init.sql`):

- **Identity:** `id` PK, `rfc822_message_id` (UNIQUE WHERE NOT NULL), `in_reply_to`, `references_chain`, `gmail_thread_id`, `gmail_labels` (JSON)
- **Headers:** `subject`, `sender_address` (normalized lowercase), `sender_name`, `sender_domain`
- **Direction:** `direction` ∈ `{inbound, outbound, self, unknown}`
- **Timestamps:** `date_sent`, `date_received` (ISO strings)
- **Body:** `body_text`, `body_html`, `body_text_source` ∈ `{plain, html2text, snippet, empty}`
- **Flags:** `is_multipart`, `has_attachments`, `attachment_count`, `is_bulk`, `bulk_signal`
- **Provenance:** `source_file_id`, `source_byte_offset`, `source_byte_length`, `raw_hash`, `body_text_hash`
- **Lifecycle:** `ingested_at`

**Indexes:**
- `idx_messages_rfc_msgid` UNIQUE WHERE rfc822_message_id IS NOT NULL
- `idx_messages_date_sent`, `idx_messages_sender_address`, `idx_messages_sender_domain`
- `idx_messages_gmail_thread`, `idx_messages_is_bulk`, `idx_messages_direction`
- `idx_messages_source_raw_hash` UNIQUE WHERE raw_hash IS NOT NULL AND source_file_id IS NOT NULL (added 003 — the idempotency anchor)

**Note:** the table is named `messages` for historical reasons; in practice it's a generic event/activity table. Health records, workouts, calendar events, etc. all use it.

---

### `recipients`
Normalized recipients per message.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `message_id` | INTEGER NOT NULL | FK → messages, ON DELETE CASCADE |
| `address` | TEXT NOT NULL | normalized lowercase |
| `name` | TEXT | |
| `rtype` | TEXT NOT NULL | CHECK ∈ `{to, cc, bcc}` |

Indexes: `idx_recipients_message`, `idx_recipients_address`.

---

### `attachments`
Metadata-only. `on_disk_path` NULL means not extracted.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `schema_type` | TEXT NOT NULL | Default `'DigitalDocument'` |
| `message_id` | INTEGER NOT NULL | FK → messages CASCADE |
| `filename`, `content_type`, `content_disposition`, `size_bytes`, `on_disk_path`, `content_hash` | | |

Indexes: `idx_attachments_message`, `idx_attachments_ctype`.

---

## Conversation grouping

### `threads`
Derived view of conversation groupings. Populated post-ingest from `messages.gmail_thread_id` (preferred) or In-Reply-To/References chains.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `schema_type` | TEXT NOT NULL | Default `'Conversation'` |
| `gmail_thread_id` | TEXT | Backwards-compat; UNIQUE WHERE NOT NULL |
| `subject_canonical` | TEXT | Subject of earliest message, Re:/Fwd: stripped |
| `message_count` | INTEGER | |
| `date_first`, `date_last` | TEXT | |
| `participants` | TEXT | JSON array of normalized addresses |
| `source_kind` | TEXT | (added 002) — `gmail`, `imessage`, etc. |
| `thread_key` | TEXT | (added 002) — source-agnostic thread identity |

Indexes: `idx_threads_gmail_id` UNIQUE, `idx_threads_date_last`, `idx_threads_kind_key` UNIQUE on `(source_kind, thread_key)`, `idx_threads_source_kind`.

**Identity migration:** post-002, `(source_kind, thread_key)` is the canonical unique key. `gmail_thread_id` is preserved for backwards compatibility.

### `message_threads`
Bridge table.

```
PRIMARY KEY (message_id, thread_id)
```

Both columns FK with CASCADE. Index `idx_msg_threads_thread` for reverse lookup.

---

## Typed tables

### `documents`
Typed table for `DigitalDocument` rows (OneDrive files, Google Drive files, Apple Notes, staged markdown). Created by migration 0008; populated by migration 0009 (existing DigitalDocument rows moved from `messages`) and by document-targeting adapters going forward.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `schema_type` | TEXT NOT NULL | Default `'DigitalDocument'` |
| `rfc822_message_id` | TEXT | UNIQUE WHERE NOT NULL (carried from messages) |
| `subject` | TEXT | Filename or document title |
| `file_path` | TEXT | Full path to source file on disk |
| `file_size` | INTEGER | File size in bytes |
| `mtime` | TEXT | Modification timestamp (ISO 8601) |
| `ctime` | TEXT | Creation timestamp (ISO 8601) |
| `body_text` | TEXT | Extracted text content (NULL for metadata-only rows) |
| `body_text_source` | TEXT | How body_text was derived |
| `body_text_hash` | TEXT | |
| `raw_hash` | TEXT | SHA-256 of raw source bytes |
| `is_bulk` | INTEGER NOT NULL DEFAULT 0 | 1 for metadata-only / reference library files |
| `source_file_id` | INTEGER | FK → source_files |
| `bucket` | TEXT | Logical grouping (e.g., "Outputs/Projects", "Reference/Mind Tools") |
| `created_at` | TEXT NOT NULL | DEFAULT ISO timestamp |

**Indexes:** `idx_documents_dedup` UNIQUE on `(source_file_id, raw_hash)` WHERE both NOT NULL, `idx_documents_path` on `file_path`, `idx_documents_bucket` on `bucket`.

**Key design:** No sender/direction/recipient columns — documents are not messages. Bucket replaces thread-based grouping. `file_path`, `file_size`, `mtime`, `ctime` are document-specific columns absent from `messages`.

---

## Embedding & search infrastructure

### `chunks`
Generic chunked-content registry (renamed from `documents` by migration 0007). Holds chunks from any source (messages, documents, people entities, vault inventories, etc.) for unified semantic search.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | (matches `doc_vectors` rowid) |
| `schema_type` | TEXT NOT NULL | Schema.org @type of source row |
| `source_table` | TEXT NOT NULL | `messages`, `documents`, `people`, `inventory_md`, etc. |
| `source_id` | INTEGER NOT NULL | FK to source row |
| `chunk_index` | INTEGER NOT NULL DEFAULT 0 | 0..N within source row |
| `chunk_strategy` | TEXT | `message_body_512tok`, etc. |
| `title` | TEXT | denormalized |
| `content` | TEXT NOT NULL | the chunk text (also FTS source) |
| `content_hash` | TEXT | sha256 for dedupe |
| `metadata_json` | TEXT | per-source structured metadata |
| `embedding_model` | TEXT | e.g., `nomic-embed-text-v1.5-Q` |
| `embedded_at` | TEXT | NULL until embedded |
| `created_at` | TEXT NOT NULL | |

Indexes: `idx_chunks_source` on `(source_table, source_id)`, `idx_chunks_schema_type`, `idx_chunks_embedded_at`, `idx_chunks_src_chunk` UNIQUE on `(source_table, source_id, chunk_index)`.

### `doc_vectors` (virtual, vec0)
sqlite-vec virtual table for semantic search. **Created at runtime** by ingest scripts after the sqlite-vec extension loads — DDL not in migration files because vec0 requires the extension loaded before the CREATE statement is parsed.

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS doc_vectors USING vec0(embedding float[768]);
```

`doc_vectors.rowid` MUST equal `chunks.id` for FK joins.

### `doc_fts` (virtual, FTS5)
Full-text index in external-content mode pointing at `chunks`.

```sql
CREATE VIRTUAL TABLE doc_fts USING fts5(
    content,
    title,
    content='chunks',
    content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);
```

Three triggers maintain sync: `chunks_ai` (after insert), `chunks_ad` (after delete), `chunks_au` (after update of content/title).

---

## Identity resolution

### `people_resolution`
Links email addresses to vault `Entities/People/*.md` notes.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `address` | TEXT NOT NULL UNIQUE | normalized lowercase email |
| `person_note_path` | TEXT | e.g., `Entities/People/Maureen Fischer.md` |
| `confidence` | REAL | 0.0–1.0 |
| `resolved_at` | TEXT NOT NULL | |
| `resolution_method` | TEXT | `exact_email`, `manual`, `fuzzy_name`, etc. |

---

## Health & geo sidecars (migration 003)

High-volume time-series telemetry that doesn't belong in `messages`.

### `record_metadata`
Apple Health `<MetadataEntry>` children of `<Record>`. Per-message KV pairs.

`(id PK, message_id FK CASCADE, key NOT NULL, value)`. Indexes on `message_id` and `key`.

### `hr_samples`
Apple Health `<InstantaneousBeatsPerMinute>` nested in Records.

`(id, parent_message_id FK, ts NOT NULL, bpm NOT NULL)`. Indexes on parent and `ts`.

### `workout_events`
Apple Health `<WorkoutEvent>` children of `<Workout>`.

`(id, workout_message_id FK, event_type, date, duration_seconds)`.

### `workout_statistics`
Apple Health `<WorkoutStatistics>` children of `<Workout>`.

Aggregate stats per workout: `stat_type`, `value_min/avg/max/sum`, `unit`, `date_start/end`.

### `geo_traces`
Shared sidecar for trajectory points. Used by:

- Apple Health workout-routes/*.gpx GPX trkpt rows
- Google Timeline timelinePath points

`(id, parent_message_id FK, source_kind NOT NULL, point_idx NOT NULL, ts, lat NOT NULL, lon NOT NULL, elevation_m, speed_mps, course, horizontal_accuracy_m, vertical_accuracy_m, extra_json)`.

Indexes on parent, source_kind, ts.

---

## Single-table @type domains

Pattern: one table per @type domain, one row per `(identity_key, instrument)`. The `instrument` column (Schema.org `Action.instrument`) identifies the tool/platform.

### `bookmarks` (migration 004)
`schema_type` = `'BookmarkAction'`. One row per `(normalized_url, instrument)`.

**Identity:** `(normalized_url, instrument)` UNIQUE.

**Instruments seen in code:** `raindrop`, `chrome-bookmarks`, `session-buddy`, `safari`, `toby`, `ie-favorites`.

**Default search filter:** `WHERE instrument='raindrop'` (Raindrop is canonical; others retained).

**Soft-exclusion:** `excluded` flag with `excluded_reason` (e.g., `junk:gmail-root`).

**Conflict behavior:** on `(normalized_url, instrument)` collision, `appearance_count` increments.

Full column list in `004_bookmarks.sql`.

### `connections` (migration 005, pending application)
`schema_type` = `'BefriendAction'`. One row per `(dedupe_key, instrument)`.

**Identity:** `dedupe_key` = `profile_url` if present, else `'name:'||name_normalized`. Modern FB takeouts emit name only — name-keyed default.

**Status enum:** `connection_status` ∈ `{active, inactive, pending_outbound, pending_inbound, rejected}`.

**Reconciliation across exports:**
- `connection_status` ← latest sighting wins
- `friends_since` ← earliest non-null
- `appearances_json` ← full audit trail of per-export observations

**Person-note reconciliation** (`person_link` → `Entities/People/*.md`) deferred — null on initial ingest; populated in a separate pass.

Full column list in `005_connections.sql`.

---

## Schema.org @type vocabulary in active use

| @type | Where |
|---|---|
| `Dataset` | `source_files.schema_type` default |
| `EmailMessage` | `messages.schema_type` default |
| `Conversation` | `threads.schema_type` default |
| `DigitalDocument` | `documents` typed table; `attachments.schema_type` default |
| `BookmarkAction` | `bookmarks.schema_type` default |
| `BefriendAction` | `connections.schema_type` default |
| `Message` | non-email `messages` rows |
| `CreativeWork` | staged-md ingester (per-@type frontmatter override) |
| `ListenAction` | Spotify rows |

`messages.schema_type` is overridden by ingesters — Apple Health rows, workouts, calendar events, etc. carry their domain @types. DigitalDocument rows live in the `documents` typed table (not `messages`) as of migration 0009.

---

## Triggers in active use

```sql
-- chunks → doc_fts sync (renamed from documents_ai/ad/au in migration 0007)
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks ...
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks ...
CREATE TRIGGER chunks_au AFTER UPDATE OF content, title ON chunks ...
```

No other triggers per migrations 0001–0009.

---

## Behavior-preservation notes for the new framework

When porting to the new framework, the following must be reproduced exactly:

1. **The `(source_file_id, raw_hash)` partial unique index** is the idempotency anchor. Every adapter must populate both columns, and on conflict the row is skipped (INSERT OR IGNORE pattern).
2. **The `schema_type` column on every domain table** must remain — it's the JSON-LD export hook.
3. **The dual-dimension `(file_kind, source_kind)` separation** in `source_files` is intentional. Don't collapse them.
4. **Threads identity is `(source_kind, thread_key)`** post-002. New adapters set both. `gmail_thread_id` is the legacy backwards-compat path; do not extend it for new sources.
5. **`chunks.id` = `doc_vectors.rowid` invariant** — the embed pipeline depends on this. New code must not introduce a separate ID space.
6. **The three FTS triggers** (`chunks_ai/ad/au`) must be preserved exactly — FTS5 external-content mode is not self-maintaining.
7. **`documents`, `bookmarks`, and `connections` patterns** are the canonical model for typed tables. New @type domains follow the same shape: own table with domain-specific columns + dedup index.
8. **All ISO-8601 timestamps** use the `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` format as the default — preserve the millisecond precision.

---

## Tables NOT in migrations 0001–0009

`init_db.py` and the runtime ingest scripts can create additional tables on the fly. The following are known to exist but live outside the migration system:

- `doc_vectors` (vec0) — created at runtime after sqlite-vec extension loads
- Any per-script staging or temp tables — should be enumerated by the schema-inventory step in Phase 0

The new framework should formalize these via the migration system rather than runtime DDL, **except** vec0 which fundamentally requires extension-load-then-create ordering (document the runtime-DDL exception and isolate it in one place).

---

## Verifying this snapshot against the live DB

Before depending on this document for the rewrite, run:

```sql
SELECT migration_id, applied_at FROM schema_migrations ORDER BY migration_id;
SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name;
SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' ORDER BY tbl_name, name;
SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name;
```

Diff the output against this document. Any discrepancy is a snapshot bug to fix here before Phase 1 begins.
