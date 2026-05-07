# Writing a New Adapter

An adapter translates a source format (mbox, SQLite database, CSV, JSON export, etc.) into rows that the personal-history-db framework inserts, deduplicates, and indexes.

## Minimal adapter

```python
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy


class MyAdapter(Adapter):
    name = "my_source"
    source_kind = "my_source"
    file_kind = "csv"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for line in source_path.read_text().splitlines():
            yield AdapterRow(
                body_text=line,
                body_text_source="plain",
                date_sent="2024-01-15T00:00:00Z",
            )
```

Save this as a `.py` file anywhere. Add its directory to the `adapter_paths` list in your instance config (or pass it as a CLI flag), and the framework discovers it automatically.

## Required class attributes

| Attribute | Type | Purpose |
|:---|:---|:---|
| `name` | `str` | Unique identifier used in logs, CLI, and the adapter registry |
| `source_kind` | `str` | Written to `source_files.source_kind` — identifies the data origin (e.g., `"gmail"`, `"imessage"`) |
| `file_kind` | `str` | Written to `source_files.file_kind` — identifies the file format (e.g., `"mbox"`, `"sqlite"`, `"csv"`) |
| `schema_type` | `str` | Default Schema.org `@type` for rows (e.g., `"EmailMessage"`, `"Message"`) |
| `dedup_strategy` | `DedupStrategy` | How the adapter produces dedup keys — one of `RFC822_MESSAGE_ID`, `PLATFORM_SYNTHETIC`, `SOURCE_POSITION`, `CONTENT_HASH` |

Optional: `batch_size` (default 500) controls how often the framework commits.

## Required method: `iter_rows()`

```python
def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
```

Yield one `AdapterRow` per row to insert. The framework handles:

- Source file registration
- INSERT OR IGNORE into the `messages` table
- Recipient and attachment sidecar inserts
- Batch commits
- `body_text_hash` computation (if you don't set it)
- `raw_hash` computation (fallback — prefer setting it yourself from raw source bytes)
- Direction inference via `IdentitySettings`
- Progress logging
- `IngestReport` construction

For parse errors, log and skip rather than raising — this keeps the ingest running past malformed records.

## AdapterRow fields

The full field list is in [base.py](../src/phdb/adapters/base.py). Key fields:

| Field | When to set |
|:---|:---|
| `schema_type` | Always — your source's Schema.org `@type` |
| `rfc822_message_id` | Email sources only — the RFC822 Message-ID |
| `subject` | When available |
| `sender_address` | When available (normalized lowercase) |
| `sender_name` | When available |
| `sender_domain` | Derived from sender_address |
| `direction` | Leave as `"unknown"` — the framework infers it from `IdentitySettings` |
| `date_sent` | ISO 8601 string, timezone-aware |
| `body_text` | The main text content |
| `body_text_source` | How body_text was derived: `"plain"`, `"html2text"`, etc. |
| `is_bulk` / `bulk_signal` | Pre-populate if your source has bulk detection logic |
| `source_byte_offset` / `source_byte_length` | For file-position-based resume support |
| `raw_hash` | SHA-256 of raw source bytes (for dedup integrity) |
| `recipients` | List of `{"address": str, "name": str, "rtype": "to"|"cc"|"bcc"}` dicts |
| `attachments` | List of `{"filename": str, "content_type": str, "size_bytes": int}` dicts |

## DedupStrategy

| Strategy | When to use | Dedup key |
|:---|:---|:---|
| `RFC822_MESSAGE_ID` | Email (mbox, EML) | `rfc822_message_id` column, UNIQUE partial index |
| `PLATFORM_SYNTHETIC` | Platform exports (Discord, Facebook) | Adapter constructs a synthetic key |
| `SOURCE_POSITION` | Positional sources (CSV rows) | `source_file_id` + `source_byte_offset` |
| `CONTENT_HASH` | Fallback | `raw_hash` column |

The `messages` table has a partial UNIQUE index on `rfc822_message_id WHERE rfc822_message_id IS NOT NULL`. For non-email sources, dedup relies on `raw_hash` uniqueness (which you should set).

## Optional overrides

### `parse_date(raw: str) -> str | None`

Override for source-specific date formats. Default passes through unchanged.

### `compute_raw_hash(row: AdapterRow) -> str`

Override to compute the hash from raw source bytes instead of the default synthetic hash.

### `detect_bulk(row: AdapterRow) -> tuple[bool, str | None]`

Override for source-specific bulk detection. Default returns `(False, None)`.

### `infer_direction(row, identity) -> str`

Rarely needs overriding — the default checks sender/recipients against `IdentitySettings.is_me()`.

### `_register_source(conn, source_path) -> int`

Override to populate additional `source_files` columns (e.g., `source_org`, `file_size`). See the mbox adapter for an example.

### `run(source_path, conn, settings) -> IngestReport`

Override for features like resume support or time budgets. Call `super().run()` for the standard pipeline. See the mbox adapter for the resume pattern.

## Adapter discovery

The framework discovers adapters by scanning directories listed in `settings.adapter_paths`. For each `.py` file (excluding `_`-prefixed files), it imports the module and finds all concrete `Adapter` subclasses.

- **Project adapters** live in `src/phdb/adapters/` (shipped with the framework)
- **Instance adapters** live in your instance config's adapter directory
- When two adapters share a `name`, the later path wins (instance overrides project)

## Testing your adapter

1. Write a synthetic fixture file in your source format
2. Create a temp DB with migrations applied:
   ```python
   from phdb.db import connect
   from phdb.migrations.runner import MigrationRunner

   db_path = tmp_path / "test.db"
   with connect(db_path) as conn:
       MigrationRunner(conn).apply_pending()
   ```
3. Run the adapter:
   ```python
   adapter = MyAdapter()
   settings = Settings.load(db_path=db_path)
   with connect(db_path) as conn:
       report = adapter.run(source_path, conn, settings)
   assert report.rows_inserted == expected_count
   ```
4. Test direction inference by setting `settings.identity`:
   ```python
   settings.identity = IdentitySettings(owner_emails={"me@example.com"})
   ```

## Reference implementation

The [mbox adapter](../src/phdb/adapters/mbox.py) is the reference implementation. It demonstrates:

- Custom streaming parser for performance
- Bulk detection inside `iter_rows()` (needs raw message headers)
- HTML-to-text body conversion with fallback
- Attachment metadata extraction
- Resume support via byte offset tracking
- `_register_source()` override for `source_org` and `file_size`
- `run()` override for resume state computation
- Constructor parameters for `source_kind` / `source_org` configurability
