# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development

```bash
cd personal-history-db/
uv venv && uv pip install -e ".[dev]"
```

Always run commands via `uv run` to avoid stale venv issues:

```bash
uv run pytest                          # all tests
uv run pytest tests/test_mbox_adapter.py  # single file
uv run pytest -k "test_bulk_detection"    # single test by name
uv run pytest -xvs                     # verbose, stop on first failure
uv run ruff check src/ tests/          # lint
uv run ruff format src/ tests/         # auto-format
uv run mypy src/                       # type check
```

CLI entry point:

```bash
uv run phdb --help
uv run phdb --instance-dir /path/to/instance migrate
uv run phdb --instance-dir /path/to/instance ingest path/to/file.mbox --adapter mbox --dry-run
uv run phdb --instance-dir /path/to/instance stats
```

## Architecture

Three-tier separation (code / instance / data):
- **Project** (`personal-history-db/`) — publishable framework, no PII
- **Instance** (`personal-history-instance/`) — owner-specific config, identity, custom adapters, migrations 1000+
- **Data** (`personal-history-data/`) — SQLite DB, embeddings, source files

### Core modules

| Module | Purpose |
|:---|:---|
| `src/phdb/cli.py` | Click CLI — `migrate`, `ingest`, `stats`, `query`, `embed` |
| `src/phdb/db.py` | SQLite connection factory (WAL mode, busy_timeout, sqlite-vec) |
| `src/phdb/settings.py` | Three-tier config merge: Pydantic defaults -> instance TOML -> env vars (`PHDB_` prefix) |
| `src/phdb/log.py` | Structured logging with PII redaction filter |
| `src/phdb/query.py` | Unified query layer: hybrid search, lookups, discovery, people queries |
| `src/phdb/embed_service.py` | Ollama embedding client for semantic search |
| `src/phdb/adapters/base.py` | `Adapter` ABC + `AdapterRow` dataclass + `DedupStrategy` enum |
| `src/phdb/adapters/loader.py` | `discover_adapters()` — dynamic import from configured paths |
| `src/phdb/adapters/mbox.py` | Reference adapter: Gmail mbox streaming parse, bulk detection, resume |
| `src/phdb/migrations/runner.py` | `MigrationRunner` — project 0001-0999, instance 1000+ |

### Adapter contract

Subclass `Adapter`, set 5 class attributes (`name`, `source_kind`, `file_kind`, `schema_type`, `dedup_strategy`), implement `iter_rows()` yielding `AdapterRow`. The framework handles insert, dedup, direction inference, batching, and reporting. See `docs/writing-an-adapter.md`.

### Migration numbering

- Project migrations: `src/phdb/migrations/project/0001_*.sql` through `0999_*.sql`
- Instance migrations: `1000_*.sql`+ (loaded from instance dir at runtime)
- Tracked in `schema_migrations` table; `MigrationRunner.apply_pending()` applies in numeric order

### Query layer

`src/phdb/query.py` is the single query path. All 11 MCP tools and the CLI `query` subcommand delegate here. Key functions: `search()` (hybrid semantic+FTS+RRF), `get_message()`, `get_chunk()`, `get_thread()`, `list_sources()`, `corpus_stats()`, `nearest_neighbors()`, `server_info()`, `find_messages_by_participant()`, `find_threads_by_subject()`, `top_correspondents()`. All take `conn: sqlite3.Connection` as first arg — the module is stateless.

`server.py` is a thin MCP wrapper — each `@mcp.tool()` calls one query function. Config resolution: `PHDB_DB_PATH` env var > `PHDB_INSTANCE_DIR` (loads Settings from TOML) > `PERSONAL_HISTORY_DB` (legacy) > `./personal-history.db` fallback.

### Database

SQLite with WAL mode. Core tables: `source_files`, `messages`, `recipients`, `attachments`, `documents`, `doc_vectors` (vec0 virtual table, 768-dim nomic-embed-text). Specialized: `threads`, `bookmarks`, `connections`, health sidecars. Never run two writers concurrently (WAL serializes but busy_timeout=30s can still conflict).

## Rewrite plan status

Tracked in `REWRITE_PLAN.md`. Phases are sequential and exit-gated. Current state is in the most recent `System/Handoffs/` file. Key phases:

- Phase 1 (complete): Project scaffold, CLI, migrations, base adapter, test suite
- Phase 2 (complete): Mbox reference adapter, adapter loader, authoring guide
- Phase 3 (complete): Instance scaffold, PII extraction into instance TOML
- Phase 4 (complete): Bulk adapter port (20 adapters, golden-diff validated)
- Phase 5 (complete): Query layer cutover (query.py unified path, retrieve.py retired)
- Phase 6 (complete): Embed pipeline hardening (writelock, chunking, batch embed)
- Phase 7 (complete): Physical split (project/instance/data to separate directories)
- Phase 8 (next): Polish & publish

## Conventions

- **Dry-run by default**: All ingest commands default to `--dry-run`; `--apply` required for real writes
- **Idempotent**: Adapters use dedup strategies; re-running is safe
- **No PII in project tier**: All identity config (emails, phone numbers, names) lives in instance TOML, loaded via `IdentitySettings`
- **Golden-diff validation**: When porting a legacy ingester, diff output against the legacy script on real data before retiring it
- **Test fixtures are synthetic**: `tests/fixtures/` contains generated data only, never real user data
- **Python 3.11+**, strict mypy, ruff with `select = ["E", "F", "W", "I", "UP", "B", "SIM"]`
- **`uv` is the package manager** (not pip directly)

## MCP server

`server.py` runs as an MCP server for Claude Code and Claude Desktop. Configure in `.claude/settings.json`:

```json
"mcpServers": {
  "personal-history-db": {
    "command": "uv",
    "args": ["--directory", "/path/to/personal-history-db", "run", "python", "server.py"],
    "env": {
      "PHDB_DB_PATH": "/path/to/personal-history-data/personal-history.db",
      "PHDB_INSTANCE_DIR": "/path/to/personal-history-instance"
    }
  }
}
```
