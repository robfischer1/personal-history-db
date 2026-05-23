# personal-history-db

**A plugin host for centralizing scattered personal digital history
into a single queryable SQLite store.** Pip-install a source plugin
and ingest data without modifying phdb itself.

Built around SQLite + [sqlite-vec](https://github.com/asg017/sqlite-vec)
for hybrid semantic + keyword search with reciprocal-rank fusion.
Ships as both a CLI tool and an [MCP](https://modelcontextprotocol.io/)
server for integration with AI assistants.

## What it does

Your personal digital history is scattered across dozens of formats —
Gmail mbox exports, iMessage backups, Discord ZIPs, Apple Health XML,
Spotify streaming history, Goodreads CSV, Raindrop bookmarks,
phone SMS / call logs, Apple Notes, OneDrive snapshots, and more.
phdb is the host that ingests, deduplicates, embeds, and queries
across all of them via a single SQLite database.

The host is small and stable. The interesting work happens in
**plugins** — one per data source, declared via a `plugin.toml`
manifest, implemented against the `PhdbSourcePlugin` ABC.

- **Source plugins** ingest one data source into typed action rows
  (`EmailMessage`, `ChatMessage`, `BookmarkAction`, `Observation`, …).
- **Facet plugins** subscribe to source emissions and project them
  into a facet node graph (Person, Place, Time, Thread, Topic).
- Both are discoverable via Python entry points — first-party plugins
  ship in-tree under `src/phdb/{plugins,facets}/`; third-party plugins
  pip-install as `phdb-plugin-<name>` distributions.

## Quickstart

```bash
# Clone and install
git clone https://github.com/robfischer1/personal-history-db.git
cd personal-history-db
uv venv && uv pip install -e ".[dev]"

# Scaffold an instance directory (identity, paths, embedding config)
phdb init ~/personal-history-instance

# Edit the generated TOML files with your info, then migrate
phdb --instance-dir ~/personal-history-instance migrate

# See what plugins are installed (34 first-party plugins ship in-tree)
phdb plugin list

# Ingest via plugin (preview first if desired)
phdb --instance-dir ~/personal-history-instance \
    plugin ingest mbox ~/takeout/All\ mail.mbox

# Check what's in the database
phdb --instance-dir ~/personal-history-instance stats

# Semantic search (requires Ollama running with nomic-embed-text)
phdb --instance-dir ~/personal-history-instance \
    query "that conversation about moving to New York"
```

See [docs/fresh-start.md](docs/fresh-start.md) for a complete
zero-to-query walkthrough.

## Architecture — 4 layers

phdb stacks four layers, each with a well-defined contract:

| Layer | Lives at | What it does |
| :--- | :--- | :--- |
| **core** | `src/phdb/core/` | Plugin ABC contract, manifest loader, EmissionBus, migration runner, DB connection factory, embed pipeline, write lock. Source-agnostic infrastructure. |
| **schemas** | `src/phdb/schemas/` | Canonical Schema.org-keyed typed-table definitions (`EmailMessage`, `BookmarkAction`, `Observation`, `WebPage`, …). Plugins declare which schemas they `emit`; the loader validates. |
| **facets** | `src/phdb/facets/` | First-party facet plugins (people, places, time, threads, topics). Subscribe to source emissions; build identity-coalesced node graphs. |
| **plugins** | `src/phdb/plugins/` | First-party source plugins — one per data source (`mbox`, `raindrop`, `apple_health`, `spotify`, `discord`, …). Implement `discover` / `parse` / `ingest_row`. |

Physical layout:

```
personal-history-db/            # Project tier (this repo) — no PII
├── src/phdb/
│   ├── cli.py                  # Click CLI: migrate, ingest, stats, query, plugin
│   ├── core/                   # Plugin contract, manifest, loader, EmissionBus
│   ├── schemas/                # Canonical typed-table schemas
│   ├── facets/                 # First-party facet plugins
│   ├── plugins/                # First-party source plugins
│   ├── formats/                # Pure parsers + shared upsert helpers
│   ├── records/                # Typed record dataclasses
│   └── migrations/project/     # Schema migrations 0001-0028
└── tests/                      # synthetic fixtures + 600+ tests

personal-history-instance/      # Instance tier (private) — owner identity
├── identity.toml               # Emails, phones, names for direction inference
├── paths.toml                  # DB path
├── embedding.toml              # Model name, endpoint, dimensions
└── identity_rules.toml         # Facet coalescence rules (Phase 8+)

personal-history-data/          # Data tier (private) — the actual database
└── personal-history.db         # SQLite + sqlite-vec (WAL mode)
```

The three-tier separation (project / instance / data) keeps the
publishable host PII-free while letting users keep identity config and
DB on private disks.

See [docs/architecture.md](docs/architecture.md) for the full design.

## Installation

```bash
pip install personal-history-db
```

Installs the core host plus all 34 first-party plugins (raindrop,
mbox, gmail, apple_health, spotify, discord, imessage, google_voice,
phone_sms, phone_calls_xml, sms_xml, chat_logs, facebook_unified,
facebook_connections, google_contacts, google_fit, google_drive,
google_timeline, google_activity, google_voice, apple_notes_full,
apple_dbs, apple_health_backup, strong, goodreads, amazon, articles,
clippings, claude_chat, claude_code, calendar, onedrive, phone_photos,
staged_md, writing_deltas, readaction). Five first-party facet
plugins (people, places, time, threads, topics) ship alongside as
Phase 4 skeletons.

For local development:

```bash
git clone https://github.com/robfischer1/personal-history-db.git
cd personal-history-db
uv venv && uv pip install -e ".[dev]"
```

## Writing a plugin

```bash
phdb plugin scaffold notion_export \
    --emits="DigitalDocument" \
    --formats-used="notion_md" \
    --kind=source
```

Generates a canonical plugin layout under
`src/phdb/plugins/notion_export/`. Fill in the parser + upsert helpers,
ship via in-tree commit or pip-installable distribution.

See [**docs/plugins.md**](docs/plugins.md) for the full author guide —
quick start, worked example, manifest reference, ABC method reference,
shared helpers, testing patterns, discovery + distribution, common
pitfalls.

## First-party plugins (in-tree)

phdb ships 34 source plugins and 5 facet plugins. Source plugins
live at `src/phdb/plugins/<name>/`; facet plugins at
`src/phdb/facets/<name>/`. Each is self-contained — manifest +
plugin class + ingest helpers + (optional) per-plugin tests.

| Category | Plugins |
| :--- | :--- |
| Email | `mbox` (Gmail Takeout, Thunderbird, Apple Mail) |
| Chat | `discord`, `imessage`, `apple_dbs`, `facebook_unified`, `phone_sms`, `sms_xml`, `google_voice`, `chat_logs` (AIM/MSN) |
| AI sessions | `claude_chat`, `claude_code` |
| Bookmarks | `raindrop`, `apple_dbs` (Safari history) |
| Contacts | `google_contacts`, `facebook_connections` |
| Health / fitness | `apple_health`, `apple_health_backup`, `google_fit`, `strong` |
| Media | `spotify`, `goodreads`, `phone_photos` |
| Files / drives | `google_drive`, `onedrive`, `apple_notes_full`, `articles`, `clippings`, `amazon`, `staged_md` |
| Location / activity | `google_timeline`, `google_activity` |
| Calls / calendar | `phone_calls_xml`, `calendar` |
| Original writing | `writing_deltas`, `readaction` |
| Facets | `people`, `places`, `time`, `threads`, `topics` |

Browse [src/phdb/plugins/](src/phdb/plugins/) and
[src/phdb/facets/](src/phdb/facets/) for the full inventory.

## Status — v0.4.0

v0.4.0 transitions phdb from a monolithic adapter codebase
(`phdb.adapters.*`) to a plugin host (`phdb.plugins.*` +
`phdb.facets.*`). All 34 first-party adapters have ported; the legacy
`phdb ingest --adapter <name>` CLI path is retired in favor of
`phdb plugin ingest <name>`. There is no compatibility shim — per the
plan's Phase 0 Q14 decision, the break is hard.

If you're upgrading from v0.3.x:

- `from phdb.adapters.raindrop import RaindropAdapter` →
  `from phdb.plugins.raindrop import RaindropPlugin` (and the class
  exposes `run(path, conn, settings)` with the same surface).
- `phdb ingest --adapter mbox <path>` → `phdb plugin ingest mbox <path>`.
- Third-party adapters that used the v0.3 `Adapter` base class need to
  re-implement against `PhdbSourcePlugin`. See
  [docs/plugins.md](docs/plugins.md) for the contract.

## MCP server

The MCP server lives in a separate package — [personal-history-db-mcp](https://github.com/robfischer1/personal-history-db-mcp).
It exposes 12 tools for AI assistants (Claude Code, Claude Desktop,
etc.):

`search`, `get_message`, `get_chunk`, `get_thread`, `list_sources`,
`corpus_stats`, `nearest_neighbors`, `server_info`,
`find_messages_by_participant`, `find_threads`,
`top_correspondents`, `log_engagement`

Install via uvx:

```bash
uvx --from git+https://github.com/robfischer1/personal-history-db-mcp.git personal-history-db-mcp
```

Plugins can register their own MCP tools via `register_tools(server)`;
those tools merge into the same MCP surface alongside the core 12.

See [docs/configuration.md](docs/configuration.md) for
Claude Code / Desktop setup.

## Features

- **34 first-party source plugins + 5 facet plugins** discoverable via
  Python entry points; in-tree or pip-installable
- **Hybrid retrieval** — vec0 semantic search + FTS5 keyword search
  + reciprocal-rank fusion (RRF)
- **4-layer architecture** — core / schemas / facets / plugins, each
  with a stable contract
- **Three-tier deployment** — publishable project code (no PII),
  private instance config (identity, API keys), data directory
  (DB, source files) in separate directories
- **Dedup strategies** — RFC822 Message-ID, platform-synthetic keys,
  source-position, content-hash
- **Direction inference** — automatic inbound/outbound/self
  classification using owner identity config
- **Embedding pipeline** — chunking, batched Ollama embedding
  (nomic-embed-text, 768-dim), cross-process write lock
- **EmissionBus** — source plugins emit facet projections at ingest
  time; subscribed facet plugins coalesce into a node graph
- **Pluggable embedding** — `EmbedProvider` Protocol with Ollama
  implementation; extensible to other backends
- **Safe preview** — `--dry-run` flag on ingest/embed parses and
  reports without writing

## Development

```bash
uv run pytest                  # 600+ tests
uv run ruff check src/ tests/  # lint
uv run mypy src/               # type check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development
guide.

## Documentation

- [Fresh Start](docs/fresh-start.md) — zero-to-query walkthrough
  for new adopters
- [Configuration](docs/configuration.md) — TOML settings, env vars,
  MCP server setup
- [Architecture](docs/architecture.md) — 4-layer design, module map,
  data flow
- [**Plugins**](docs/plugins.md) — plugin author guide (manifest, ABC,
  shared helpers, testing, distribution, pitfalls)
- [Records](docs/RECORDS.md) — typed record dataclasses produced by
  format parsers
- [Database Schema](CURRENT-SCHEMA.md) — table definitions and
  relationships
- [MCP Contract](MCP-CONTRACT.md) — MCP tool signatures and behavior
- [Changelog](CHANGELOG.md) — release history

## License

[Apache 2.0](LICENSE)
