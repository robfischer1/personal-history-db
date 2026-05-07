---
adapter_name: mbox
legacy_path: System/Tools/personal-history-db/ingest_mbox.py
source_format: mbox
unique_key_strategy: rfc822_message_id
update_policy: skip
date_handling:
  primary_field: Date (RFC822 header)
  format: RFC 2822 date string via email.utils.parsedate_to_datetime
  fallbacks:
    - Received header (last semicolon-delimited date, iterated newest-first)
gotchas:
  - sys.stdout.reconfigure(encoding="utf-8") for Windows console
  - Connection factory must set busy_timeout=30000
  - Dedup keys must be the source primary key (rfc822_message_id), never domain identifiers -- see Strong app incident
  - Windows path handling -- pathlib.Path, never raw string concatenation
  - All datetime parsing must produce timezone-aware UTC values
  - Core Data date columns drift across migrations -- not directly applicable here, but the general principle applies to any future mbox-adjacent SQLite source
  - html2text is a runtime dependency (used for HTML-to-plain body conversion on non-bulk messages)
  - Messages without a Message-ID header yield rfc822_message_id=None -- the UNIQUE index is partial (WHERE rfc822_message_id IS NOT NULL), so NULL-keyed messages fall through dedup and rely on raw_hash or content_hash as a secondary safeguard
  - The legacy streaming parser does NOT use stdlib mailbox.mbox -- it splits on bare From-space line boundaries for performance on multi-GB files; this must be preserved
  - Gmail labels header (X-Gmail-Labels) is comma-separated, stored as a JSON array string -- not a raw CSV
  - BULK_NOREPLY_PATTERNS regex must match the legacy pattern exactly; it encodes institutional knowledge about sender-local-part patterns
  - Body cap at 200,000 chars (MAX_BODY_LEN) and snippet cap at 280 chars (SNIPPET_LEN) are defensive limits that must be preserved
  - Resume support depends on byte-offset arithmetic (source_byte_offset + source_byte_length) -- the adapter must track and yield these per row
  - Error rows in the legacy code use None placeholders to keep batch alignment; in the new framework errors should be logged and the row skipped (iter_rows simply does not yield for error rows)
  - ROB_ADDRS is PII -- it MUST NOT appear in the project-tier adapter; direction inference is handled by IdentitySettings
schema_quirks:
  - The messages table UNIQUE index on rfc822_message_id is partial (WHERE IS NOT NULL) -- two messages with NULL Message-ID will both insert successfully, which is correct behavior for malformed email
  - source_files.source_org is set to Google Takeout by the legacy script but the base class _register_source does not populate it -- the adapter must either override _register_source or set source_org post-registration
  - source_files gained source_kind in migration 002 -- the base class _register_source already passes self.source_kind, so this is handled
  - The legacy script sets schema_type=Dataset on the source_files row explicitly; the base class uses the default Dataset from the DDL, which matches
  - The legacy script does not populate body_html (the column was added in the framework AdapterRow but not in the legacy INSERT) -- the port should populate body_html with the raw HTML parts for non-bulk messages before conversion, enabling downstream consumers to re-render if needed
  - Attachments schema gained on_disk_path and content_hash columns in the framework that the legacy script does not use -- these should be left NULL
output_table: messages (with sidecar rows in recipients and attachments)
atom_emission: []
---

## Mapping logic

### Source: Gmail .mbox file (Google Takeout export)

Each message in the mbox is delimited by a line starting with `From ` (the mbox "From_" separator). The legacy parser reads the file as raw bytes, splits on these boundaries, and feeds each chunk to `email.message_from_bytes()`.

### Field-by-field mapping to AdapterRow

| AdapterRow field | Source | Transform |
|:---|:---|:---|
| `schema_type` | hardcoded | `"EmailMessage"` |
| `rfc822_message_id` | `Message-ID` header | Strip angle brackets `<>`, strip whitespace; None if absent |
| `in_reply_to` | `In-Reply-To` header | Strip angle brackets, strip whitespace; None if empty |
| `references_chain` | `References` header | Raw header value; None if absent |
| `gmail_thread_id` | `X-GM-THRID` header | Raw value; None if absent |
| `gmail_labels` | `X-Gmail-Labels` header | Comma-split, strip each, JSON-encode the list; None if empty |
| `subject` | `Subject` header | MIME-decoded via `email.header.decode_header` + `make_header` |
| `sender_address` | `From` header | `email.utils.parseaddr` then lowercase/strip |
| `sender_name` | `From` header | MIME-decoded display name; None if empty |
| `sender_domain` | derived from `sender_address` | Split on `@`, take the domain part; None if no `@` |
| `direction` | derived | **Adapter does NOT set this.** Yields `"unknown"` and lets `base.run()` call `infer_direction()` with `IdentitySettings`. The legacy hardcoded `ROB_ADDRS` set is replaced entirely. |
| `date_sent` | `Date` header | `email.utils.parsedate_to_datetime` then `.isoformat()`. Must produce timezone-aware output. |
| `date_received` | `Received` headers | Iterate all `Received` headers, extract the date after the last semicolon in each, return the first one that parses successfully. |
| `body_text` | message body | For non-bulk: full plain-text body (all text/plain parts joined by double-newline), falling back to html2text conversion of text/html parts. For bulk: first 280 chars of plain-text only (no HTML conversion). Capped at 200,000 chars. |
| `body_html` | message body | **New in port**: raw HTML parts joined, for non-bulk messages. None for bulk. The legacy script discarded raw HTML after conversion; the framework AdapterRow has a `body_html` field that should be populated. |
| `body_text_source` | derived | One of: `"plain"`, `"html2text"`, `"plain-snippet"`, `"html-conv-failed"`, `"empty"` |
| `is_multipart` | `msg.is_multipart()` | 1 or 0 |
| `has_attachments` | derived from attachment scan | 1 if any attachment parts found, else 0 |
| `attachment_count` | derived | Count of attachment parts |
| `is_bulk` | multi-signal detection | Adapter pre-populates in `iter_rows()` -- see Bulk Detection section below |
| `bulk_signal` | multi-signal detection | String naming the first matching signal |
| `source_byte_offset` | streaming parser | Byte offset of the `From ` line that starts this message |
| `source_byte_length` | streaming parser | Total byte length of the raw message chunk |
| `raw_hash` | `hashlib.sha256(raw_bytes)` | SHA-256 hex digest of the raw message bytes. Adapter must compute this itself because the base class default hashes a synthetic string, not the raw bytes. |
| `body_text_hash` | derived | SHA-256 hex digest of body_text if non-None. The base `run()` already computes this when missing, so the adapter can omit it. |

### Sidecar: recipients

Each recipient is extracted from the To, Cc, and Bcc headers using `email.utils.getaddresses()`. Each yields a dict with keys: `address` (normalized lowercase), `name` (MIME-decoded or None), `rtype` (one of `"to"`, `"cc"`, `"bcc"`).

These are placed in `AdapterRow.recipients`. The base class `_insert_sidecars()` handles the INSERT.

### Sidecar: attachments

Each MIME part with `Content-Disposition: attachment` or a non-None `get_filename()` (excluding multipart container parts) yields a dict with keys: `filename` (MIME-decoded), `content_type` (MIME type), `content_disposition` (raw header), `size_bytes` (length of decoded payload).

These are placed in `AdapterRow.attachments`. The base class `_insert_sidecars()` handles the INSERT (and passes NULL for `on_disk_path` and `content_hash`, which the legacy script also did not populate).

## Bulk detection

The adapter MUST run bulk detection inside `iter_rows()` before yielding, and set `is_bulk` and `bulk_signal` directly on the AdapterRow. This is because bulk detection in the legacy code operates on the parsed `email.message.Message` object -- it inspects raw headers (List-Unsubscribe, List-Id, Precedence, Auto-Submitted, X-Auto-Response-Suppress) and the sender local part -- and these headers are not available on the AdapterRow.

The base class `run()` calls `detect_bulk()` after `iter_rows()` yields, but only sets the fields if they are still at their defaults (0, None). Since the adapter pre-populates them, the base class check becomes a no-op.

The six-signal detection order (preserved from legacy):

1. `List-Unsubscribe` header present
2. `List-Id` header present
3. `Precedence` header is `bulk`, `list`, or `junk` (case-insensitive)
4. `Auto-Submitted` header present and not `"no"` (case-insensitive)
5. `X-Auto-Response-Suppress` header present
6. Sender local part matches BULK_NOREPLY_PATTERNS regex

The regex pattern must be preserved exactly:

```
(no-?reply|donotreply|do-not-reply|notification|notifications|alerts?|
updates?|news|newsletter|marketing|promo|deals?|broadcast|announce|
automated|mailer|noreply)
```

**Bulk affects body extraction**: When `is_bulk` is true, the body is truncated to a 280-char snippet of plain text only (no html2text conversion). This coupling means bulk detection MUST happen before body extraction, both of which happen inside `iter_rows()`.

## Streaming parser

The legacy `stream_messages()` function is a custom mbox parser that MUST be preserved in the adapter (as a private method or module-level function). It:

1. Opens the file in binary mode (`"rb"`)
2. Supports `skip_to_offset` for resume: seeks to the offset and starts reading from the next `From ` boundary
3. Accumulates lines into a `bytearray` buffer
4. Yields `(raw_bytes, byte_offset, byte_length)` when a new `From ` line is encountered
5. Yields the final buffer at EOF

This is materially faster than `mailbox.mbox` for multi-GB files because it avoids the stdlib full message indexing pass.

## Resume support

The legacy script implements resume via byte offsets:

1. On start, query `MAX(source_byte_offset + source_byte_length)` from `messages WHERE source_file_id = ?`
2. Pass this as `skip_to_offset` to `stream_messages()`
3. The streaming parser seeks to that offset and starts from the next `From ` boundary

**In the new framework**: The base class `run()` does NOT implement resume. The adapter `iter_rows()` must handle resume internally.

**Design gap**: The `run()` method passes only `source_path` to `iter_rows()`. The adapter needs the `conn` and `source_file_id` to compute the resume offset. Options:

1. **Override `run()`** partially to inject resume state into `iter_rows()` via a stored attribute before calling `super().run()`
2. **Store resume state as an instance attribute** set during a pre-run hook
3. **Accept that the first port may not support resume** and add resume to the base class as a follow-up

This is flagged for human decision. The resume feature is essential for production use (the Gmail mbox is multi-GB and routinely times out).

## Time budget

The legacy `--max-seconds` flag checks `time.time() - t_start` every 250 messages and breaks the loop if exceeded. This interacts with resume: the next run picks up where the time budget stopped.

**In the new framework**: The base class `run()` has no time-budget concept. The adapter `iter_rows()` can implement this internally (stop yielding when budget is exceeded), but the start time and budget value need to be passed in. This is the same kwargs-passing problem as resume.

**Recommendation**: Time budget and resume are tightly coupled (budget stops the run, resume restarts it). Both should be addressed together, either via base class enhancement or adapter-level `run()` override.

## SQLite interactions

### Connection setup

Handled entirely by `phdb.db.connect()`. The adapter MUST NOT open its own connection. The connection factory already sets:

- `PRAGMA journal_mode = WAL`
- `PRAGMA synchronous = NORMAL`
- `PRAGMA temp_store = MEMORY`
- `PRAGMA mmap_size = 268435456`
- `PRAGMA cache_size = -65536`
- `PRAGMA busy_timeout = 30000`
- `PRAGMA foreign_keys = ON`

The legacy script additionally loaded sqlite_vec at connection time. The framework `connect()` takes `load_vec=False` by default; the mbox adapter does not need vec operations during ingest, so the default is correct.

### Transaction batching

The base class `run()` commits every `self.batch_size` rows (default 500, matching legacy `BATCH_SIZE = 500`). The adapter need not manage transactions.

### Source file registration

The base class `_register_source()` handles the `INSERT ... ON CONFLICT(source_path) DO UPDATE SET ingested_at` pattern. However, the legacy script also sets:

- `schema_type = 'Dataset'` (matches DDL default)
- `source_org = 'Google Takeout'` (NOT set by base class -- base passes None)
- `file_kind = 'mbox'` (set via `self.file_kind`)
- `file_size` (NOT set by base class)
- `file_hash = NULL` (legacy also passes NULL)

**Gap**: `source_org` and `file_size` are not populated by the base class. The adapter should either:
- Override `_register_source()` to add these fields
- Post-update the row after registration

`source_org` should be `"Google Takeout"` -- this is adapter metadata, not PII, and belongs in the project-tier adapter code.

### Recipient and attachment inserts

Handled by the base class `_insert_sidecars()`. The adapter populates `AdapterRow.recipients` and `AdapterRow.attachments` and the base class does the rest.

### Message count update

The base class `run()` updates `source_files.message_count` after completion. The legacy script does the same in its `finally` block.

### Indexing

No adapter-specific indexes needed. The init migration covers:
- `idx_messages_rfc_msgid` (UNIQUE, partial)
- `idx_messages_date_sent`
- `idx_messages_sender_address`
- `idx_messages_sender_domain`
- `idx_messages_gmail_thread`
- `idx_messages_is_bulk`
- `idx_messages_direction`

## Direction inference

The legacy script hardcodes `ROB_ADDRS` -- a set of 7 email addresses belonging to the database owner. This is PII and MUST NOT appear in the project-tier adapter.

In the new framework, direction is inferred by `Adapter.infer_direction(row, identity)` using `IdentitySettings.is_me(address)`. The identity settings are populated from the instance-tier TOML config (e.g., `identity.toml`).

The adapter `iter_rows()` should yield rows with `direction="unknown"`. The base class `run()` will then call `infer_direction()` if identity settings are populated.

The direction logic matches the legacy exactly:
- sender_is_me AND any_recipient_is_me => `"self"`
- sender_is_me => `"outbound"`
- any_recipient_is_me => `"inbound"`
- else => `"unknown"`

**Note**: The base class `infer_direction()` checks `row.recipients` (the list of dicts), while the legacy code checked a flat list of recipient addresses. The base class implementation already handles this correctly by iterating `row.recipients` and calling `identity.is_me(r.get("address", ""))`.

## Edge cases

### Messages without Message-ID

Some malformed emails lack a `Message-ID` header. The legacy code sets `rfc822_message_id = None`. The UNIQUE index on `rfc822_message_id` is partial (`WHERE rfc822_message_id IS NOT NULL`), so these rows always insert. This means duplicate messages without Message-IDs will create duplicate rows. The `raw_hash` field provides a secondary dedup signal for downstream consumers but is NOT enforced at the DB level.

**Recommendation**: The adapter should log a warning counter for NULL-Message-ID rows so the operator knows how many are undeduped.

### MIME decoding failures

The legacy code wraps all `decode_header`/`make_header` calls in try/except, falling back to `str(value)`. The adapter must preserve this -- MIME headers in the wild contain every possible encoding violation.

### Charset decoding failures

Body part payloads use `part.get_content_charset() or "utf-8"` with `errors="replace"`. A secondary fallback catches `LookupError` (unknown charset name) and retries with `utf-8`. Both must be preserved.

### Empty body

When no plain-text or HTML parts are found, `body_text` is None and `body_text_source` is `"empty"`. This is valid and expected for attachment-only messages.

### HTML conversion failure

If `html2text.handle()` raises, the legacy code returns `(None, "html-conv-failed")`. The adapter should catch this and set `body_text_source` accordingly rather than letting the exception propagate.

### Extremely large messages

The 200,000-char body cap is a defensive limit against pathological messages (e.g., base64 inline images decoded to megabytes of text). The 280-char snippet cap for bulk is a storage optimization (bulk bodies are low-value). Both must be preserved.

### File-level errors

The legacy code catches per-message exceptions, increments an error counter, logs the first 10, and continues. The adapter `iter_rows()` should implement the same pattern: catch exceptions around `message_from_bytes()` and the parsing pipeline, log them, and continue to the next message rather than aborting.

### Gmail-specific header absence

Non-Gmail mbox files will not have `X-GM-THRID` or `X-Gmail-Labels` headers. These fields will be None, which is correct. The adapter should work for any RFC 2822 mbox, not just Gmail exports, though Gmail is the primary use case.

### Timezone-naive dates

`parsedate_to_datetime` can return timezone-naive datetimes for malformed `Date` headers that lack timezone info. The adapter should force UTC when no timezone is present:

```python
if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
```

The legacy code does NOT do this -- it calls `.isoformat()` on whatever `parsedate_to_datetime` returns, which may produce naive ISO strings. This is a bug that the port should fix.

### "From " line in message body

The mbox format requires `>From ` escaping for lines starting with `From ` inside message bodies. The streaming parser correctly handles this because it only splits on `From ` at the start of a line in the raw byte stream, and `email.message_from_bytes()` handles the content-transfer-encoding. However, poorly formed mbox exports (some non-Gmail sources) may not escape properly. This is a known mbox format limitation, not an adapter bug.

## Adapter class attributes

```python
name = "mbox"
source_kind = "gmail"            # Backfill value from migration 002; consider making configurable for non-Gmail mbox
file_kind = "mbox"
schema_type = "EmailMessage"
dedup_strategy = DedupStrategy.RFC822_MESSAGE_ID
batch_size = 500
```

**Open question**: `source_kind` is `"gmail"` for the primary use case, but the adapter parses generic RFC 2822 mbox. If a non-Gmail mbox is ever ingested, `source_kind` should differ. Options:
- Hardcode `"gmail"` and require a separate adapter instance for other mbox sources
- Accept `source_kind` as a constructor argument with default `"gmail"`

Flag for human decision.

## What iter_rows() must yield

For each successfully parsed message, yield one `AdapterRow` with:

1. All header-derived fields populated (`schema_type`, `rfc822_message_id`, `in_reply_to`, `references_chain`, `gmail_thread_id`, `gmail_labels`, `subject`, `sender_address`, `sender_name`, `sender_domain`)
2. `direction = "unknown"` (let the base class infer it)
3. `date_sent` and `date_received` as ISO-8601 strings (timezone-aware)
4. `is_bulk` and `bulk_signal` pre-populated from the 6-signal detector (because detection requires the raw `email.message.Message` object)
5. `body_text` and `body_text_source` with bulk-aware extraction (snippet for bulk, full for non-bulk)
6. `body_html` populated for non-bulk messages (new in port)
7. `is_multipart`, `has_attachments`, `attachment_count` flags
8. `source_byte_offset` and `source_byte_length` from the streaming parser
9. `raw_hash` as SHA-256 of the raw message bytes
10. `recipients` list of dicts with `address`/`name`/`rtype`
11. `attachments` list of dicts with `filename`/`content_type`/`content_disposition`/`size_bytes`

For parse errors, do NOT yield -- log the error and continue.

## What the base class handles (adapter must NOT reimplement)

- **Source file registration** (`_register_source`)
- **INSERT OR IGNORE into messages** (`_insert_row`)
- **Recipient and attachment inserts** (`_insert_sidecars`)
- **Batch commit cadence** (every `batch_size` rows)
- **Final commit + source_files.message_count update**
- **body_text_hash computation** (if the adapter does not set it, `run()` computes it)
- **raw_hash fallback computation** (if the adapter does not set it, `run()` computes a synthetic hash -- but the adapter SHOULD set it from raw bytes for correctness)
- **Direction inference** via `infer_direction()` + `IdentitySettings`
- **Progress logging** via the phdb logger
- **IngestReport construction** (`rows_yielded`, `rows_inserted`, `rows_skipped`, `errors`)

## What the adapter must handle itself

- **Streaming mbox parser** (custom `From ` line splitter, not stdlib)
- **RFC 822 header parsing** (`email.message_from_bytes`)
- **MIME header decoding** (`decode_header` + `make_header` with fallback)
- **Bulk detection** (6-signal, must run before body extraction)
- **Body extraction** (plain/HTML, bulk-aware truncation, html2text conversion)
- **Attachment metadata extraction** (filename, content_type, size_bytes)
- **Recipient extraction** (To/Cc/Bcc via `getaddresses`)
- **Date parsing** (RFC 2822 format, Received header fallback)
- **raw_hash computation** from raw message bytes
- **Resume support** (byte-offset query + streaming parser seek) -- requires framework enhancement or `run()` override
- **Time budget** -- requires framework enhancement or `run()` override
- **Per-message error handling** (catch, log, skip -- do not abort)

## PII that must NOT be in the project-tier adapter

The following values from the legacy script are PII belonging to the database owner and MUST be configured via instance-tier `identity.toml`, never hardcoded:

```python
# Legacy pattern (DO NOT reproduce — this is what we're replacing):
OWNER_ADDRS = {
    "owner@example.com",
    "owner-alias@example.com",
    # ... additional aliases ...
}
```

These map to `IdentitySettings.owner_emails` in the framework three-tier settings. Real values go in `identity.toml`, never in project code.

## Dependencies

- **stdlib**: `email`, `email.header`, `email.utils`, `hashlib`, `json`, `re`, `time` (all in Python stdlib)
- **third-party**: `html2text` (must be added to `pyproject.toml` dependencies)
- **framework**: `phdb.adapters.base.Adapter`, `phdb.adapters.base.AdapterRow`, `phdb.adapters.base.DedupStrategy`, `phdb.settings.IdentitySettings`

`html2text` is NOT currently in `pyproject.toml` dependencies. It must be added for this adapter to work.

## Estimated test fixture coverage

The Fixture Generator should produce synthetic data to exercise:

1. **Happy path**: A well-formed 3-message mbox with To/Cc/Bcc recipients, plain-text body, and one attachment
2. **Bulk detection -- all 6 signals**: One message per signal type (List-Unsubscribe, List-Id, Precedence:bulk, Auto-Submitted:auto-replied, X-Auto-Response-Suppress, noreply@ sender)
3. **Body extraction paths**: plain-text only, HTML-only (triggers html2text), multipart/alternative with both, empty body (attachment-only message)
4. **MIME encoding**: Subject with RFC 2047 encoded-word, sender name with encoded-word, filename with encoded-word
5. **Charset edge cases**: message body in ISO-8859-1, message body in Windows-1252, message body with unknown charset (triggers fallback)
6. **Missing Message-ID**: A message with no Message-ID header (tests NULL dedup key behavior)
7. **Duplicate Message-ID**: Two messages with the same Message-ID (tests INSERT OR IGNORE)
8. **Gmail headers**: Message with X-GM-THRID and X-Gmail-Labels; message without them
9. **Date edge cases**: Missing Date header (should yield None), timezone-naive Date, Received-header-only date
10. **Large body**: Message body exceeding 200,000 chars (tests MAX_BODY_LEN cap)
11. **Bulk snippet**: Bulk message with plain body >280 chars (tests SNIPPET_LEN truncation)
12. **Resume**: Two-pass ingest of the same file -- first pass inserts N messages, second pass should skip them and insert 0
13. **Direction inference**: Messages where sender is owner (outbound), recipient is owner (inbound), both are owner (self), neither is owner (unknown)
14. **Attachment metadata**: Message with multiple attachments of different types; verify filename, content_type, size_bytes extraction
15. **Error resilience**: Corrupted message bytes that cause `message_from_bytes` to raise -- verify the adapter logs and continues
16. **"From " line in body**: Message containing an un-escaped `From ` line in the body (tests streaming parser robustness)
