# personal-history-db

A framework for ingesting, deduplicating, embedding, and querying personal digital history across dozens of source formats — email archives, chat logs, social media exports, phone backups, fitness trackers, and more.

Built around SQLite + [sqlite-vec](https://github.com/asg017/sqlite-vec) for hybrid semantic + keyword search with reciprocal-rank fusion. Ships as both a CLI tool and an [MCP](https://modelcontextprotocol.io/) server for integration with AI assistants.

## Features

- **32 adapters** covering Gmail mbox, iMessage, Discord, Facebook, Apple Health, Google Takeout (Fit, Timeline, Voice, Activity, Drive, Contacts), Spotify, Goodreads, Strong, Raindrop bookmarks, phone SMS/MMS/call logs, Apple Notes, OneDrive, and more
- **Hybrid retrieval** — vec0 semantic search + FTS5 keyword search + reciprocal-rank fusion (RRF)
- **Three-tier architecture** — publishable project code (no PII), private instance config (identity, API keys), and data directory (DB, source files) live in separate directories
- **Dedup strategies** — RFC822 Message-ID, platform-synthetic keys, source-position, content-hash
- **Direction inference** — automatic inbound/outbound/self classification using owner identity config
- **Embedding pipeline** — chunking, batched Ollama embedding (nomic-embed-text, 768-dim), cross-process write lock
- **MCP server** — 11 tools for AI-assistant integration (search, lookups, stats, people queries)
- **Pluggable embedding** — `EmbedProvider` Protocol with Ollama implementation; extensible to other backends
- **Safe preview** — `--dry-run` flag on ingest/embed parses and reports without writing

## Quickstart

```bash
# Clone and install
git clone https://github.com/robfischer1/personal-history-db.git
cd personal-history-db
uv venv && uv pip install -e ".[dev]"

# Scaffold an instance directory (identity, paths, embedding config)
phdb init ~/personal-history-instance

# Edit the generated TOML files with your info, then:
phdb --instance-dir ~/personal-history-instance migrate

# Ingest a Gmail mbox export (preview first, then for real)
phdb --instance-dir ~/personal-history-instance \
    ingest ~/takeout/All\ mail.mbox --adapter mbox --dry-run
phdb --instance-dir ~/personal-history-instance \
    ingest ~/takeout/All\ mail.mbox --adapter mbox

# Check what's in the database
phdb --instance-dir ~/personal-history-instance stats

# Semantic search (requires Ollama running with nomic-embed-text)
phdb --instance-dir ~/personal-history-instance \
    query "that conversation about moving to New York"
```

See [docs/fresh-start.md](docs/fresh-start.md) for a complete zero-to-query walkthrough.

## Architecture

```
personal-history-db/          # Project tier (this repo) — no PII
├── src/phdb/
│   ├── cli.py                # Click CLI: migrate, ingest, stats, query, embed
│   ├── db.py                 # SQLite connection factory (WAL, pragmas, sqlite-vec)
│   ├── settings.py           # Three-tier config: defaults → TOML → env vars
│   ├── query.py              # Hybrid search, lookups, discovery, people queries
│   ├── embed_pipeline.py     # Chunking + batched Ollama embedding
│   ├── embed_service.py      # Ollama HTTP client
│   ├── writelock.py          # Cross-process file lock for DB writes
│   ├── adapters/             # 32 source-format adapters
│   │   ├── base.py           # Adapter ABC + AdapterRow + DedupStrategy
│   │   ├── loader.py         # Dynamic adapter discovery
│   │   └── mbox.py ...       # One file per source format
│   └── migrations/project/   # Schema migrations 0001–0999
├── server.py                 # MCP server (thin wrapper around query.py)
└── tests/                    # 563 tests, all synthetic fixtures

personal-history-instance/    # Instance tier (private) — owner identity, paths
├── identity.toml             # Emails, phones, names for direction inference
├── paths.toml                # DB path, adapter search paths
└── embedding.toml            # Model name, endpoint, dimensions

personal-history-data/        # Data tier (private) — the actual database
└── personal-history.db       # SQLite + sqlite-vec (WAL mode)
```

See [docs/architecture.md](docs/architecture.md) for the full design.

## Writing an adapter

```python
from pathlib import Path
from collections.abc import Iterator
from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy

class MyAdapter(Adapter):
    name = "my-source"
    source_kind = "chat"
    file_kind = "json"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs) -> Iterator[AdapterRow]:
        for record in load_my_data(source_path):
            yield AdapterRow(
                body_text=record["text"],
                sender_address=record["from"],
                date_sent=record["timestamp"],
            )
```

See [docs/writing-an-adapter.md](docs/writing-an-adapter.md) for the full guide.

## MCP server

The MCP server lives in a separate package — [personal-history-db-mcp](https://github.com/robfischer1/personal-history-db-mcp). It exposes 12 tools for AI assistants (Claude Code, Claude Desktop, etc.):

`search`, `get_message`, `get_chunk`, `get_thread`, `list_sources`, `corpus_stats`, `nearest_neighbors`, `server_info`, `find_messages_by_participant`, `find_threads`, `top_correspondents`, `log_engagement`

Install via uvx:

```bash
uvx --from git+https://github.com/robfischer1/personal-history-db-mcp.git personal-history-db-mcp
```

See [docs/configuration.md](docs/configuration.md) for Claude Code/Desktop setup.

## Development

```bash
uv run pytest                  # 563 tests
uv run ruff check src/ tests/  # lint
uv run mypy src/               # type check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development guide.

## Documentation

- [Fresh Start](docs/fresh-start.md) — zero-to-query walkthrough for new adopters
- [Configuration](docs/configuration.md) — TOML settings, env vars, MCP server setup
- [Adapters](ADAPTERS.md) — per-adapter reference: input formats, export instructions, gotchas
- [Architecture](docs/architecture.md) — three-tier design, module map, data flow
- [Writing an Adapter](docs/writing-an-adapter.md) — adapter contract, dedup strategies, testing
- [Database Schema](CURRENT-SCHEMA.md) — table definitions and relationships
- [MCP Contract](MCP-CONTRACT.md) — MCP tool signatures and behavior
- [Changelog](CHANGELOG.md) — release history

## License

[Apache 2.0](LICENSE)
