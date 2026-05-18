# RECORDS.md — phdb Typed Records Catalog

The records layer (`phdb.records`) defines typed intermediates between format parsers and vendor adapters. Each record is a frozen dataclass carrying the minimal fields needed to express one atomic unit of personal history, plus a `Provenance` object tracing its origin.

```
Source → Extractor → Format Parser → Typed Record → Vendor Adapter → AdapterRow → DB
                                      ════════════
                                      this catalog
```

---

## Shared: `Provenance`

Every record carries a `Provenance` instance (per Decision D2).

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `source_path` | `str` | yes | Absolute path to the source file |
| `source_byte_offset` | `int \| None` | no | Start offset in source (for streaming resume) |
| `source_byte_length` | `int \| None` | no | Length of the raw segment |
| `raw_hash` | `str` | yes | Content-addressed dedup key (sha256 of canonical content) |

---

## 1. `EmailMessage`

**Schema.org @type:** `EmailMessage`
**Consumed by:** `mbox` adapter
**Dedup strategy:** `rfc822_message_id` (RFC822_MESSAGE_ID)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `rfc822_message_id` | `str` | yes | Globally unique per RFC 5322 |
| `in_reply_to` | `str \| None` | no | Parent message-id |
| `references_chain` | `str \| None` | no | Space-separated ancestor IDs |
| `subject` | `str \| None` | no | |
| `sender_address` | `str` | yes | Normalized lowercase |
| `sender_name` | `str \| None` | no | Display name |
| `sender_domain` | `str \| None` | no | Extracted from address |
| `date_sent` | `str` | yes | ISO-8601 |
| `date_received` | `str \| None` | no | |
| `body_text` | `str \| None` | no | Plain-text body |
| `body_html` | `str \| None` | no | HTML body |
| `body_text_source` | `str \| None` | no | `plain`, `html_converted`, etc. |
| `is_multipart` | `bool` | yes | |
| `has_attachments` | `bool` | yes | |
| `attachment_count` | `int` | yes | |
| `gmail_thread_id` | `str \| None` | no | Gmail X-GM-THRID |
| `gmail_labels` | `list[str]` | no | Gmail X-GM-LABELS |
| `recipients` | `list[Recipient]` | no | Denormalized per D4-0.4 |
| `attachments` | `list[Attachment]` | no | First-class child records per D4-0.4 |

**Child records:**
- `Attachment` (first-class, with `parent_id = raw_hash`) — has own MIME-part byte offset

**Denormalized sub-structures (not records):**
- `Recipient` = `{address: str, name: str | None, rtype: str}` — stays as list per D4-0.4

---

## 2. `ChatMessage`

**Schema.org @type:** `Message`
**Consumed by:** `discord`, `imessage`, `facebook`, `chat_logs`, `sms_xml`, `phone_sms`, `google_voice` (texts)
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `platform_id` | `str \| None` | no | Platform-specific message ID |
| `sender_address` | `str` | yes | Phone, username, or synthetic address |
| `sender_name` | `str \| None` | no | |
| `date_sent` | `str` | yes | ISO-8601 |
| `body_text` | `str \| None` | no | |
| `is_multipart` | `bool` | yes | MMS or multi-attachment |
| `has_attachments` | `bool` | yes | |
| `attachment_count` | `int` | yes | |
| `thread_key` | `str \| None` | no | Grouping key for conversation threads |
| `recipients` | `list[Recipient]` | no | Denormalized |
| `attachments` | `list[Attachment]` | no | Child records (parent_id) |

---

## 3. `AISessionMessage`

**Schema.org @type:** `Conversation`
**Consumed by:** `claude_chat`, `claude_code`, `gemini_web`, `gemini_scribe`
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `date_sent` | `str` | yes | ISO-8601 |
| `body_text` | `str \| None` | no | |
| `kind` | `str` | yes | `message`, `tool_use`, `tool_result`, `sidechain` |
| `role` | `str` | yes | `user`, `assistant`, `system` |
| `model` | `str \| None` | no | e.g., `claude-opus-4-6` |
| `parent_uuid` | `str \| None` | no | Conversation/turn UUID |
| `tool_name` | `str \| None` | no | |
| `tool_use_id` | `str \| None` | no | |
| `payload` | `str \| None` | no | Raw JSON for non-text blocks |
| `thread_key` | `str` | yes | Session identifier |
| `thread_metadata` | `dict \| None` | no | Session-level metadata (name, url, tools, cwd) |

---

## 4. `CallRecord`

**Schema.org @type:** `Action` (subtype: phone call)
**Consumed by:** `phone_calls_xml`, `google_voice` (calls), `apple_dbs` (call handler)
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `caller_address` | `str` | yes | Phone number (normalized) |
| `callee_address` | `str \| None` | no | |
| `direction` | `str` | yes | `inbound`, `outbound`, `missed`, `rejected`, `blocked` |
| `date_start` | `str` | yes | ISO-8601 |
| `duration_seconds` | `int \| None` | no | |
| `voicemail_text` | `str \| None` | no | Transcript if voicemail |
| `call_type` | `str` | yes | `voice`, `video`, `voicemail` |

---

## 5. `WebActivity`

**Schema.org @type:** `SearchAction` | `WatchAction` | `Action`
**Consumed by:** `google_activity`, `apple_dbs` (Safari history)
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `activity_type` | `str` | yes | `search`, `watch`, `visit`, `view` |
| `url` | `str \| None` | no | |
| `title` | `str \| None` | no | Page/video title |
| `query` | `str \| None` | no | Search query (for SearchAction) |
| `date_performed` | `str` | yes | ISO-8601 |
| `duration_seconds` | `int \| None` | no | Watch/visit duration |
| `platform` | `str` | yes | `google`, `youtube`, `safari`, `chrome` |

---

## 6. `BookmarkEvent`

**Schema.org @type:** `BookmarkAction`
**Consumed by:** `raindrop`, `titaniumbackup_browser_bookmarks`, `apple_dbs` (bookmarks)
**Dedup strategy:** `(normalized_url, instrument)` composite

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `url` | `str` | yes | Raw URL |
| `normalized_url` | `str` | yes | Canonicalized for dedup |
| `title` | `str \| None` | no | |
| `description` | `str \| None` | no | |
| `tags` | `list[str]` | no | |
| `folder` | `str \| None` | no | Browser folder / Raindrop collection |
| `date_added` | `str` | yes | ISO-8601 |
| `instrument` | `str` | yes | `raindrop`, `chrome`, `firefox`, `safari`, `com-android-browser-bookmark` |
| `is_dead` | `bool` | no | Dead-link detection result (Layer 2) |

---

## 7. `CalendarEvent`

**Schema.org @type:** `Event`
**Consumed by:** `calendar`
**Dedup strategy:** `PLATFORM_SYNTHETIC` (UID from iCal)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `uid` | `str` | yes | iCal UID |
| `summary` | `str \| None` | no | Event title |
| `description` | `str \| None` | no | |
| `location` | `str \| None` | no | |
| `organizer` | `str \| None` | no | |
| `date_start` | `str` | yes | ISO-8601 |
| `date_end` | `str \| None` | no | |
| `is_all_day` | `bool` | yes | |
| `recurrence_rule` | `str \| None` | no | RRULE string |
| `attendees` | `list[str]` | no | Email addresses |

---

## 8. `Contact`

**Schema.org @type:** `Person`
**Consumed by:** `google_contacts`
**Dedup strategy:** `CONTENT_HASH` (raw_hash of vCard)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `full_name` | `str` | yes | |
| `emails` | `list[str]` | no | |
| `phones` | `list[str]` | no | Normalized |
| `organization` | `str \| None` | no | |
| `title` | `str \| None` | no | Job title |
| `birthday` | `str \| None` | no | ISO date |
| `notes` | `str \| None` | no | |
| `addresses` | `list[str]` | no | Formatted postal addresses |

---

## 9. `Connection`

**Schema.org @type:** `BefriendAction`
**Consumed by:** `facebook_connections`
**Dedup strategy:** `(dedupe_key, instrument)` composite

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `display_name` | `str` | yes | |
| `platform` | `str` | yes | `facebook`, `linkedin`, etc. |
| `connection_status` | `str` | yes | `active`, `removed`, `inactive` |
| `friends_since` | `str \| None` | no | ISO date |
| `removed_date` | `str \| None` | no | |
| `inactive_reason` | `str \| None` | no | |

---

## 10. `SocialPost`

**Schema.org @type:** `SocialMediaPosting`
**Consumed by:** `facebook_posts`, `facebook_residuals`, `titaniumbackup_twitter`
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `platform_id` | `str \| None` | no | Platform-specific post ID |
| `author_name` | `str` | yes | |
| `date_posted` | `str` | yes | ISO-8601 |
| `body_text` | `str \| None` | no | |
| `post_type` | `str` | yes | `status`, `photo`, `link`, `story`, `comment`, `share` |
| `has_attachments` | `bool` | yes | |
| `attachment_count` | `int` | yes | |
| `thread_key` | `str \| None` | no | Grouping (e.g., category:thread_dir) |
| `in_reply_to` | `str \| None` | no | Parent post ID (for comments) |

---

## 11. `Reaction`

**Schema.org @type:** `LikeAction`
**Consumed by:** `facebook_residuals`
**Dedup strategy:** `CONTENT_HASH` (raw_hash)
**Parent relationship:** `parent_id` = parent SocialPost's `raw_hash` (per D4-0.5)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `parent_id` | `str` | yes | raw_hash of parent SocialPost |
| `reactor_name` | `str` | yes | |
| `reaction_type` | `str` | yes | `like`, `love`, `haha`, `wow`, `sad`, `angry` |
| `date_reacted` | `str` | yes | ISO-8601 |
| `target_summary` | `str \| None` | no | Short description of what was reacted to |

---

## 12. `HealthObservation`

**Schema.org @type:** `Observation`
**Consumed by:** `apple_health`, `google_fit`
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `observation_type` | `str` | yes | e.g., `HKQuantityTypeIdentifierHeartRate`, `steps`, `weight` |
| `value` | `float \| None` | no | Numeric measurement |
| `unit` | `str \| None` | no | e.g., `count/min`, `kg`, `steps` |
| `date_start` | `str` | yes | ISO-8601 |
| `date_end` | `str \| None` | no | |
| `source_device` | `str \| None` | no | e.g., `Apple Watch`, `Pixel` |
| `metadata` | `dict` | no | Additional key-value pairs |

**Child records:**
- `ExerciseSet` (sub-record, `parent_id` = parent's raw_hash, per D4-0.10)

---

## 13. `ExerciseSet`

**Schema.org @type:** `ExerciseAction`
**Consumed by:** `strong`, `apple_health` (workout events)
**Dedup strategy:** `CONTENT_HASH` (raw_hash)
**Parent relationship:** `parent_id` = parent HealthObservation's `raw_hash` (per D4-0.10)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `parent_id` | `str \| None` | no | raw_hash of parent workout (None for standalone) |
| `exercise_name` | `str` | yes | |
| `set_number` | `int \| None` | no | |
| `reps` | `int \| None` | no | |
| `weight_kg` | `float \| None` | no | |
| `duration_seconds` | `int \| None` | no | |
| `distance_meters` | `float \| None` | no | |
| `date_performed` | `str` | yes | ISO-8601 |
| `workout_name` | `str \| None` | no | Parent workout label |

---

## 14. `GeoTrace`

**Schema.org @type:** `GeoShape` | `Place` | `TravelAction`
**Consumed by:** `google_timeline`
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `trace_type` | `str` | yes | `visit`, `activity`, `path` |
| `date_start` | `str` | yes | ISO-8601 |
| `date_end` | `str \| None` | no | |
| `latitude` | `float \| None` | no | Center point or start |
| `longitude` | `float \| None` | no | |
| `place_name` | `str \| None` | no | |
| `place_address` | `str \| None` | no | |
| `activity_type` | `str \| None` | no | `WALKING`, `DRIVING`, `IN_VEHICLE`, etc. |
| `confidence` | `float \| None` | no | 0.0–1.0 |
| `waypoints` | `list[dict]` | no | `[{lat, lng, ts}, ...]` for paths |

---

## 15. `MediaPlay`

**Schema.org @type:** `ListenAction` | `WatchAction`
**Consumed by:** `spotify`, `amazon` (video watches)
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `media_type` | `str` | yes | `track`, `podcast_episode`, `audiobook`, `video` |
| `title` | `str` | yes | Track/episode/video title |
| `artist` | `str \| None` | no | Artist or channel |
| `album` | `str \| None` | no | |
| `date_played` | `str` | yes | ISO-8601 |
| `duration_ms` | `int \| None` | no | How long played |
| `platform` | `str` | yes | `spotify`, `amazon_music`, `amazon_video` |
| `platform_id` | `str \| None` | no | Spotify URI, ASIN, etc. |
| `is_skipped` | `bool` | no | Played < threshold |

---

## 16. `ConsumedItem`

**Schema.org @type:** `Book` | `Product`
**Consumed by:** `goodreads`, `amazon` (orders/products)
**Dedup strategy:** `PLATFORM_SYNTHETIC` (ISBN or ASIN)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `item_type` | `str` | yes | `book`, `product`, `audiobook` |
| `title` | `str` | yes | |
| `author` | `str \| None` | no | |
| `isbn` | `str \| None` | no | |
| `asin` | `str \| None` | no | |
| `date_acquired` | `str \| None` | no | Purchase or shelved date |
| `date_consumed` | `str \| None` | no | Read/finished date |
| `rating` | `float \| None` | no | User rating |
| `review_text` | `str \| None` | no | |
| `shelves` | `list[str]` | no | Goodreads shelves |
| `platform` | `str` | yes | `goodreads`, `amazon`, `kindle` |

---

## 17. `Transaction`

**Schema.org @type:** `OrderAction`
**Consumed by:** `amazon` (orders)
**Dedup strategy:** `PLATFORM_SYNTHETIC` (order_id)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `order_id` | `str` | yes | Platform order identifier |
| `date_ordered` | `str` | yes | ISO-8601 |
| `total_amount` | `float \| None` | no | |
| `currency` | `str \| None` | no | ISO 4217 |
| `items` | `list[dict]` | no | `[{title, quantity, unit_price}, ...]` |
| `platform` | `str` | yes | `amazon` |
| `status` | `str \| None` | no | `completed`, `cancelled`, `returned` |

---

## 18. `DigitalDocument`

**Schema.org @type:** `DigitalDocument` | `CreativeWork` | `Article`
**Consumed by:** `google_drive`, `onedrive`, `staged_md`, `apple_notes_full`, `apple_dbs` (notes)
**Dedup strategy:** `CONTENT_HASH` (raw_hash)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `title` | `str \| None` | no | Filename or frontmatter title |
| `body_text` | `str \| None` | no | Extracted text content |
| `body_text_source` | `str \| None` | no | `docx`, `pdf`, `html`, `md`, `protobuf` |
| `file_path` | `str \| None` | no | Relative path within source |
| `file_size` | `int \| None` | no | Bytes |
| `created_date` | `str \| None` | no | ISO-8601 |
| `modified_date` | `str \| None` | no | |
| `bucket` | `str \| None` | no | Classification bucket (Outputs, Reference, etc.) |
| `mime_type` | `str \| None` | no | |
| `document_type` | `str \| None` | no | More specific @type from frontmatter |

---

## 19. `Photograph`

**Schema.org @type:** `Photograph`
**Consumed by:** `phone_photos`, `phone_photos_metadata`
**Dedup strategy:** `CONTENT_HASH` (raw_hash of file or metadata row)

| Field | Type | Required | Notes |
|:---|:---|:---:|:---|
| `provenance` | `Provenance` | yes | |
| `file_path` | `str` | yes | Relative path to image/video |
| `file_name` | `str` | yes | |
| `date_taken` | `str` | yes | ISO-8601 (from EXIF, filename, or DB) |
| `mime_type` | `str \| None` | no | |
| `width` | `int \| None` | no | Pixels |
| `height` | `int \| None` | no | |
| `file_size` | `int \| None` | no | Bytes |
| `latitude` | `float \| None` | no | GPS |
| `longitude` | `float \| None` | no | |
| `camera_model` | `str \| None` | no | EXIF |
| `folder` | `str \| None` | no | DCIM subfolder or album |

---

## Sub-structures (not records)

These are denormalized into parent records per Decision D4-0.4:

### `Recipient`
```python
@dataclass(frozen=True)
class Recipient:
    address: str
    name: str | None = None
    rtype: str = "to"  # to, cc, bcc
```

### `Attachment` (first-class child record)
```python
@dataclass(frozen=True)
class Attachment:
    provenance: Provenance
    parent_id: str  # raw_hash of parent EmailMessage/ChatMessage
    filename: str | None = None
    content_type: str | None = None
    content_disposition: str | None = None
    size_bytes: int | None = None
    on_disk_path: str | None = None
    content_hash: str | None = None
```

---

## Record → Adapter mapping

| Record | Current adapters that produce this data |
|:---|:---|
| `EmailMessage` | `mbox` |
| `ChatMessage` | `discord`, `imessage`, `facebook`, `chat_logs`, `sms_xml`, `phone_sms`, `google_voice` |
| `AISessionMessage` | `claude_chat`, `claude_code`, `gemini_web`, `gemini_scribe` |
| `CallRecord` | `phone_calls_xml`, `google_voice`, `apple_dbs` |
| `WebActivity` | `google_activity`, `apple_dbs` |
| `BookmarkEvent` | `raindrop`, `titaniumbackup_browser_bookmarks`, `apple_dbs` |
| `CalendarEvent` | `calendar` |
| `Contact` | `google_contacts` |
| `Connection` | `facebook_connections` |
| `SocialPost` | `facebook_posts`, `facebook_residuals`, `titaniumbackup_twitter` |
| `Reaction` | `facebook_residuals` |
| `HealthObservation` | `apple_health`, `google_fit` |
| `ExerciseSet` | `strong`, `apple_health` |
| `GeoTrace` | `google_timeline` |
| `MediaPlay` | `spotify`, `amazon` |
| `ConsumedItem` | `goodreads`, `amazon` |
| `Transaction` | `amazon` |
| `DigitalDocument` | `google_drive`, `onedrive`, `staged_md`, `apple_notes_full`, `apple_dbs` |
| `Photograph` | `phone_photos`, `phone_photos_metadata` |

---

## Design invariants

1. **Records are frozen** (D8) — immutable after creation.
2. **Records are streaming intermediates** (D0.2) — never persisted to a `records` table.
3. **Parsers yield one record per iteration** (D0.7) — base batches DB inserts.
4. **Children carry `parent_id = raw_hash`** (D1, D0.3) — content-addressed, stable across re-ingestion.
5. **Record name decouples from schema.org @type** (D4) — adapter maps to appropriate `schema_type` on `AdapterRow`.
6. **Additive-only versioning** (D6) — new fields default to `None`; removal requires major version bump.
7. **Hooks for sidecar writes** (D5, D0.6) — `pre_insert(conn, record)` + `post_insert(conn, record, inserted_id)`.
