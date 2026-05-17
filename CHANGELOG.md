# Changelog

All notable changes to personal-history-db are documented here.

This project uses [Semantic Versioning](https://semver.org/). During the 0.x series, the API may change between minor versions. A 1.0 release will be tagged once the adapter and schema contracts stabilize.

## [0.2.0] - 2026-05-17

### Added

- **`phdb init` CLI command** — scaffolds a new instance directory from templates
- **`EmbedProvider` Protocol** — pluggable embedding backends (Ollama, OpenAI, Anthropic)
- **`IdentitySettings` module** — extracted owner identity into a standalone, optional module with `is_configured()` and `pii_literals()` API
- **Decay scoring** — leaky-integrator retrieval scoring with engagement hooks; migration 0011
- **Claude chat adapter** — `claude_chat` for claude.ai data exports
- **Session UUID dedup** — migration 0010 for AI chat `source_files` dedup
- **Typed-table reshape** — `documents` table for non-message content; migrations 0007-0009
- **Adapter scaffolder** — `scripts/scaffold_adapter.py` for generating new adapter boilerplate

### Changed

- License changed from MIT to **Apache 2.0**
- `embed_service.py` renamed; Ollama client now implements `EmbedProvider` Protocol
- `Settings.load()` tolerates missing `identity.toml` gracefully
- Adapters targeting `DigitalDocument` retargeted to the new `documents` table
- Embed pipeline hydrates from both `messages` and `documents` tables

### Fixed

- UNION hydration ensures both typed tables contribute to embedding

## [0.1.0] - 2026-05-07

Initial release. Framework rewrite (Phases 1-8) from legacy single-script ingesters.

### Added

- Three-tier architecture: project / instance / data separation
- Click CLI with subcommands: `migrate`, `ingest`, `stats`, `query`, `embed`
- 32 source-format adapters (mbox, iMessage, Discord, Facebook, Apple Health, Google Takeout, Spotify, Goodreads, Strong, Raindrop, phone SMS/MMS/calls, Apple Notes, OneDrive, and more)
- Hybrid retrieval: sqlite-vec semantic search + FTS5 keyword + reciprocal-rank fusion
- Adapter ABC with 5 dedup strategies (message_id, platform_key, source_position, content_hash, composite)
- Direction inference using owner identity config
- Embed pipeline: chunking, batched Ollama embedding, cross-process write lock
- MCP server with 11 query tools
- Migration runner: project 0001-0999, instance 1000+
- PII redaction in structured logging
- 563 tests with synthetic fixtures
- Full documentation set (architecture, configuration, writing-an-adapter, fresh-start)

[0.2.0]: https://github.com/robfischer1/personal-history-db/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/robfischer1/personal-history-db/releases/tag/v0.1.0
