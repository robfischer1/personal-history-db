# Architecture

## Three-tier separation

personal-history-db separates concerns into three independent directories:

| Tier | Contains | Git-tracked? | PII? |
|:---|:---|:---|:---|
| **Project** (`personal-history-db/`) | Framework code, adapters, migrations 0001–0999, tests | Yes (public) | No |
| **Instance** (`personal-history-instance/`) | Owner identity, DB path, embedding config, custom adapters, migrations 1000+ | Optional (private) | Yes |
| **Data** (`personal-history-data/`) | SQLite database, source files, media | No | Yes |

This means the project repo can be published without leaking personal information. The instance tier holds everything that makes it *yours* — your email addresses, phone numbers, name variants, and platform handles used for direction inference. The data tier holds the actual database and source files.

## Module map

```
src/phdb/
├── cli.py                 Click CLI entrypoint
│                          Subcommands: migrate, ingest, stats, query, embed
│
├── db.py                  SQLite connection factory
│                          WAL mode, busy_timeout=30s, sqlite-vec loading
│                          Two variants: connect() (context manager) and
│                          connect_persistent() (long-lived, for MCP server)
│
├── settings.py            Three-tier configuration
│                          Pydantic defaults → instance TOML → PHDB_ env vars
│                          Nested models: EmbeddingSettings, IdentitySettings
│
├── query.py               Unified query layer (stateless, conn-first)
│                          Hybrid search: vec0 + FTS5 + reciprocal-rank fusion
│                          11 public functions, one per MCP tool
│
├── embed_pipeline.py      Chunking + embedding pipeline
│                          2048-char chunks with 200-char overlap
│                          Batched Ollama calls via EmbedClient
│
├── embed_service.py       Ollama HTTP client
│                          Handles batching, retries, model warm-up
│
├── writelock.py           Cross-process file lock
│                          OS-level (msvcrt on Windows, fcntl on Unix)
│                          Prevents concurrent ingest/embed collisions
│
├── log.py                 Structured logging with PII redaction
│                          Replaces owner identity strings with [REDACTED]
│
├── validation.py          Instance config validation warnings
│
├── adapters/
│   ├── base.py            Adapter ABC, AdapterRow dataclass, DedupStrategy enum
│   ├── loader.py          discover_adapters() — dynamic import from paths
│   └── *.py               32 source-format adapters
│
├── atoms/
│   └── registry.py        Schema.org @type registry (project + instance types)
│
└── migrations/
    ├── runner.py           MigrationRunner — applies project + instance SQL
    └── project/            0001_init.sql through 0005_connections.sql
```

## Data flow

### Ingest pipeline

```
Source file (mbox, SQLite, JSON, CSV, XML, HTML, ...)
    │
    ▼
Adapter.iter_rows()          Parse source format, yield AdapterRow per record
    │
    ▼
Adapter.run()                Register source file, batch inserts, dedup,
    │                        direction inference via IdentitySettings
    ▼
messages table               Raw messages with sender, date, body, direction
    │
    ├──► recipients table    To/CC/BCC per message
    └──► attachments table   Filename, content type, size per attachment
```

### Embed pipeline

```
messages table
    │
    ▼
embed_pipeline.chunk()       Split body_text into 2048-char chunks
    │                        with 200-char overlap, min 50 chars
    ▼
EmbedClient.embed_batch()    Ollama nomic-embed-text (768-dim vectors)
    │
    ▼
documents table              One row per chunk (text, hash, position)
    │
    ▼
doc_vectors table            vec0 virtual table (768-dim float vectors)
```

### Query pipeline

```
User query string
    │
    ├──► EmbedClient          Embed the query string
    │       │
    │       ▼
    │    vec0 KNN search      Semantic similarity (cosine distance)
    │       │
    ├──► FTS5 search          Keyword matching with stopword filtering
    │       │                 AND query first, fallback to OR
    │       │
    ▼       ▼
Reciprocal-rank fusion        Merge ranked lists (K=60)
    │
    ▼
Ranked results with context   Message metadata + surrounding chunks
```

## Database schema (key tables)

| Table | Purpose |
|:---|:---|
| `source_files` | Registry of ingested files (path, adapter, org, timestamps) |
| `messages` | Core table — one row per message/event/record |
| `recipients` | To/CC/BCC sidecar for email-type messages |
| `attachments` | File metadata sidecar |
| `documents` | Chunked text for embedding (foreign key to messages) |
| `doc_vectors` | vec0 virtual table — 768-dim float vectors |
| `threads` | Conversation threading for email |
| `bookmarks` | URL bookmarks (Raindrop, browser exports) |
| `connections` | Social connections (Facebook friends, etc.) |
| `schema_migrations` | Migration tracking |

See [CURRENT-SCHEMA.md](../CURRENT-SCHEMA.md) for the full DDL.

## Adapter contract

Every adapter declares five class attributes and implements one method:

```python
class MyAdapter(Adapter):
    name = "my_source"              # unique identifier
    source_kind = "my_source"       # source_files.source_kind
    file_kind = "csv"               # source_files.file_kind
    schema_type = "Message"         # Schema.org @type for rows
    dedup_strategy = DedupStrategy.CONTENT_HASH  # how to dedup

    def iter_rows(self, source_path, **kwargs) -> Iterator[AdapterRow]:
        ...
```

The framework handles everything else: source registration, batched INSERT OR IGNORE, dedup key computation, direction inference, recipient/attachment sidecars, progress logging, and IngestReport construction.

See [writing-an-adapter.md](writing-an-adapter.md) for the full guide.

## Configuration resolution

Settings merge in priority order (highest wins):

1. CLI flags / explicit arguments
2. Environment variables (`PHDB_DB_PATH`, `PHDB_INSTANCE_DIR`, etc.)
3. Instance TOML files (`identity.toml`, `paths.toml`, `embedding.toml`)
4. Pydantic model defaults

See [configuration.md](configuration.md) for the full reference.
