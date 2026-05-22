# DB_SCHEMA.md — Personal History Database

Theme-park map for AI agents. Read this before planning any DB work.

**DB location:** `personal-history-data/personal-history.db` (7.3 GB, SQLite WAL mode)
**Schema version:** migration 0022 (messages + legacy tables dropped)
**Last updated:** 2026-05-22

---

## Domain Tables

Tables that hold typed records. Each maps to a Schema.org `@type`.

| Table | @type | Rows | Adapters | Purpose |
|:------|:------|-----:|:---------|:--------|
| `observations` | Observation | 5,968K | apple-health, apple-health-backup, google-fit | Health metrics (HR, calories, steps, etc.) |
| `chat_messages` | Message | 462K | chat-logs, imessage, discord, sms-xml, phone-sms, gmail-sms-backfill, google-voice | Chat/SMS messages |
| `search_actions` | SearchAction | 124K | calendar, google-activity | Web/app searches |
| `emails` | EmailMessage | 68K | gmail | Gmail emails |
| `conversations_messages` | Conversation | 62K | claude-code, claude-chat, gemini-web, gemini-scribe | AI session messages |
| `exercise_actions` | ExerciseAction | 45K | strong, apple-health, google-fit | Workouts and activity |
| `listen_actions` | ListenAction | 44K | spotify | Music listens |
| `watch_actions` | WatchAction | 35K | calendar, google-activity, amazon | Video/media watches |
| `actions` | Action | 29K | calendar, iphone-callhistory, iphone-waterminder, calls-xml | Generic actions (calls, water intake, etc.) |
| `events` | Event | 7,290 | calendar, facebook | Calendar/social events |
| `products` | Product | 6,513 | amazon | Product views/purchases |
| `order_actions` | OrderAction | 2,141 | amazon | Purchase orders |
| `like_actions` | LikeAction | 1,483 | facebook | Social likes |
| `persons` | Person | 1,431 | google-contacts | Contact records |
| `social_postings` | SocialMediaPosting | 595 | facebook, titaniumbackup-twitter | Social media posts |
| `comments` | Comment | 430 | facebook | Social comments |
| `places` | Place | 399 | google-timeline | Location records |
| `travel_actions` | TravelAction | 354 | google-timeline | Travel segments |
| `geo_shapes` | GeoShape | 347 | google-timeline | Geographic boundaries |
| `books` | Book | 249 | goodreads | Book records |
| `medical_records` | MedicalRecord | 198 | apple-health | Medical records |
| `reviews` | Review | 140 | amazon | Product reviews |
| `invite_actions` | InviteAction | 51 | facebook | Event invitations |
| `creative_works` | CreativeWork | 37 | staged-md | Creative works |
| `web_pages` | WebPage | 30 | iphone-safari-bookmarks | Safari bookmarks |
| `join_actions` | JoinAction | 8 | facebook | Group joins |
| `digital_documents` | DigitalDocument | 5 | staged-md | Misc documents |
| `things` | Thing | 1 | staged-md | Misc things |
| `documents` | DigitalDocument | 2,324 | obsidian-vault, onedrive | Vault notes, OneDrive files |
| `articles` | Article | 219 | obsidian-vault | Vault article notes |
| `clippings` | Clipping | 14 | obsidian-vault | Vault web clippings |
| `photographs` | Photograph | 9,232 | digikam | Photo metadata from DigiKam + (pending: phone-camera) |
| `bookmarks` | BookmarkAction | 15,757 | raindrop | Raindrop bookmark exports |
| `connections` | Person | 744 | facebook-connections | Facebook friend connections |

## Infrastructure Tables

| Table | Rows | Purpose |
|:------|-----:|:--------|
| `source_files` | 472 | Registry of every ingested file — source_org, file_kind, source_kind |
| `schema_migrations` | 22 | Applied migration tracker |

## Graph Tables (Universal Triple Store)

RDF-style triple store (migration 0012). **12.9M triples** — all cross-table relationships (threading, recipients, sidecars, chunks, attachments) expressed as triples.

| Table | Rows | Purpose |
|:------|-----:|:--------|
| `nodes` | 12,448,315 | Entity nodes — label, kind, source_table, source_id |
| `predicates` | 35 | Predicate vocabulary with 4-tier classification |
| `triples` | 12,859,633 | Subject-predicate-object assertions with provenance |
| `qualifiers` | 0 | Triple qualifiers (reserved for future use) |

### Node Kinds

| Kind | Count | Purpose |
|:-----|------:|:--------|
| `record` | 6,858,780 | Typed-table rows (observations, chat_messages, emails, etc.) |
| `sidecar` | 5,222,002 | Health sidecar rows (record_metadata, hr_samples, geo_traces, etc.) |
| `chunk` | 254,111 | Embedding text chunks |
| `thread` | 65,438 | Conversation/email threads |
| `photograph` | 20,655 | DigiKam photo metadata |
| `timestamp` | 9,047 | DigiKam timestamps |
| `attachment` | 6,427 | Email file attachments |
| `location` | 4,177 | DigiKam locations |
| `file` | 3,851 | Vault files |
| `contact` | 2,384 | Recipient email/phone addresses |
| `concept` | 1,409 | Abstract concepts (tags, topics) |
| `person` | 34 | Named persons |

### Predicate Tiers

| Tier | Count | Meaning | Who may create/curate |
|:-----|------:|:--------|:----------------------|
| `system` | 7 | Structural infrastructure (hasChunk, hasAttachment, sidecar ownership) | Ingesters only; never AI-curated |
| `derived` | 8 | Re-derivable from source data (inThread, sentTo, locatedAt, prev/next) | Ingesters recompute; AI may read |
| `knowledge` | 15 | Semantic relationships (mentions, taggedWith, depicts, authoredBy) | AI + human curated |
| `rob` | 5 | Rob-exclusive (partOf, wantsTo, outOf, wentTo) | Rob only; ingesters must not touch |

## Embedding / Search Tables

| Table | Rows | Purpose |
|:------|-----:|:--------|
| `chunks` | 254,111 | Embedding text chunks — source_table + source_id polymorphic FK; also expressed as `hasChunk` triples (230K) |
| `chunk_scores` | 250,933 | Decay/engagement scores per chunk |
| `doc_fts` | 254,111 | FTS5 full-text search index |
| `doc_vectors` | ~255K | sqlite-vec vector index (virtual table) |

## Attachments Table

| Table | Rows | Triple equivalent | Status |
|:------|-----:|:------------------|:-------|
| `attachments` | 6,427 | 6,378 `hasAttachment` triples | Standalone table + triples |

Legacy `threads`, `recipients`, and `message_threads` tables were **dropped** by migration 0022. Their data lives in the triple store: `inThread` triples (6.86M), `sentTo` triples (507K), and `thread` nodes (65K).

## Health Sidecar Tables (Legacy FK → triples emitted)

| Table | Rows | Parent table | Triples |
|:------|-----:|:-------------|:--------|
| `record_metadata` | 2,430K | observations | 2,429,627 `hasMetadata` triples |
| `hr_samples` | 934K | observations | 934,214 `hasHeartRateSample` triples |
| `geo_traces` | 1,851K | exercise_actions | 1,846,851 `hasGeoTrace` triples |
| `workout_events` | 4,729 | exercise_actions | 4,729 `hasWorkoutEvent` triples |
| `workout_statistics` | 2,343 | exercise_actions | 2,343 `hasWorkoutStatistic` triples |

## Other Tables

| Table | Rows | Purpose |
|:------|-----:|:--------|
| `commit_authorship` | 290 | Git commit → author mapping |
| `commit_authorship_repos` | 3 | Tracked git repos for commit authorship |
| `contact_name_lookup` | 578 | Name resolution for message senders |
| `people_resolution` | 580 | Cross-source person dedup |
| `engagements` | 0 | Engagement tracking (decay system, Phase 6 of Decay Policy) |
| `writing_sessions` | 0 | Obsidian writing session tracking |
| `writing_deltas` | 0 | Keystroke deltas from writing sessions |

---

## Messages Decomposition Status

Active plan: `Outputs/Plans/Messages Decomposition.md`

29 schema_types decomposed into typed tables. **messages table DROPPED** (migration 0022). **12.9M triples** emitted for all cross-table relationships. Legacy tables (`threads`, `recipients`, `message_threads`) also dropped — data lives in triple store. Adapters rewritten to emit triples directly.

| Phase | Type | Rows | Target table | Status |
|:------|:-----|-----:|:-------------|:-------|
| 1 | Observation | 5,968K | `observations` | **DONE** |
| 2 | Message | 462K | `chat_messages` | **DONE** |
| 3 | SearchAction | 124K | `search_actions` | **DONE** |
| 4 | EmailMessage | 68K | `emails` | **DONE** |
| 5 | Conversation | 62K | `conversations_messages` | **DONE** |
| 6 | ExerciseAction | 45K | `exercise_actions` | **DONE** |
| 7 | ListenAction | 44K | `listen_actions` | **DONE** |
| 8 | WatchAction | 35K | `watch_actions` | **DONE** |
| 9 | Action | 29K | `actions` | **DONE** |
| 10 | Photograph | 262 | `photographs` (existing) | **DONE** |
| 11 | Event | 7,290 | `events` | **DONE** |
| 12 | Product | 6,513 | `products` | **DONE** |
| 13 | OrderAction | 2,141 | `order_actions` | **DONE** |
| 14 | LikeAction | 1,483 | `like_actions` | **DONE** |
| 15 | Person | 1,431 | `persons` | **DONE** |
| 16 | SocialMediaPosting | 595 | `social_postings` | **DONE** |
| 17 | Comment | 430 | `comments` | **DONE** |
| 18 | Place | 399 | `places` | **DONE** |
| 19 | TravelAction | 354 | `travel_actions` | **DONE** |
| 20 | GeoShape | 347 | `geo_shapes` | **DONE** |
| 21 | Book | 249 | `books` | **DONE** |
| 22 | MedicalRecord | 198 | `medical_records` | **DONE** |
| 23 | Review | 140 | `reviews` | **DONE** |
| 24 | InviteAction | 51 | `invite_actions` | **DONE** |
| 25 | CreativeWork | 37 | `creative_works` | **DONE** |
| 26 | WebPage | 30 | `web_pages` | **DONE** |
| 27 | JoinAction + DigitalDocument + Thing | 14 | `join_actions`, `digital_documents`, `things` | **DONE** |
| 28 | — | 12.9M | All cross-table triples (threading, recipients, sidecars, chunks, attachments) | **DONE** |
| 29 | — | — | DROP messages + legacy tables; adapter triple emission; query/scoring rewrite | **DONE** |
