# Changelog

All notable changes to personal-history-db are documented here.

This project uses [Semantic Versioning](https://semver.org/). During the 0.x series, the API may change between minor versions. A 1.0 release will be tagged once the adapter and schema contracts stabilize.

## [0.4.0b0] - 2026-05-23

phdb pivots from a monolithic-adapter codebase to a **plugin host** for
centralizing scattered personal digital history. The `Adapter` ABC is
gone; sources are now `PhdbSourcePlugin` subclasses discovered via
Python entry points. The codebase is a 4-layer stack — `phdb.core/`
(connection, embed, scoring, graph, search, plugin loader) +
`phdb.schemas/` (canonical Schema.org-keyed typed table registry) +
`phdb.facets/` (Person / Place / Time / Thread / Topic facet plugins,
skeleton consumers pending Phase 8's coalescer) + `phdb.plugins/` (34
first-party source plugins, all ported from the dissolved adapter
collection).

### Breaking changes (Q14 hard break, no shim)

- `phdb.adapters` package **removed entirely**. The `Adapter` ABC + typed-table mapper at `phdb.adapters.base`, the `discover_adapters` loader at `phdb.adapters.loader`, and every per-source `phdb.adapters.<name>` module no longer exist. Replace `from phdb.adapters.<name> import <Name>Adapter` with `from phdb.plugins.<name> import <Name>Plugin`.
- `phdb ingest --adapter <name>` CLI command removed. Use `phdb plugin ingest <name> <path>` instead.
- `bookmarks` table reshaped (migration 0028): `url`, `normalized_url`, `title`, `excerpt`, `cover_url` dropped — URL identity lives only in `web_pages` joinable via `web_page_id`. Replace `SELECT url FROM bookmarks` with `SELECT wp.url FROM bookmarks b JOIN web_pages wp ON b.web_page_id = wp.id`. Unique index moved from `(normalized_url, instrument)` to `(web_page_id, instrument)`.
- `scripts/scaffold_adapter.py` retired (emitted dead-code adapter scaffolds). Use `phdb plugin scaffold <name>` or `scripts/scaffold_plugin.py` instead.

### Added

- **`phdb.core/` package** — source-agnostic infrastructure (DB connection, embed pipeline, scoring, graph service, hybrid search, plugin loader, registry).
- **`phdb.schemas/` package** — 33 canonical typed-table dataclasses keyed by Schema.org `@type`; DDL generator + `upsert_<entity>()` helper generator + migration diff against `sqlite_master`.
- **`phdb.core.plugin/` sub-package** — `PluginManifest` + `PhdbSourcePlugin` / `PhdbFacetPlugin` ABCs + entry-point loader; runtime-validated contract per Q4.
- **`phdb.facets/` package** — 5 first-party facet plugins (people, places, time, threads, topics) + `EmissionBus` for source → facet dispatch. Skeleton consumers; Phase 8 ships the rules engine.
- **`phdb.plugins/`** — 34 first-party source plugins ported from the dissolved adapter collection (amazon, apple_dbs, apple_health, apple_health_backup, apple_notes_full, articles, calendar, chat_logs, claude_chat, claude_code, clippings, discord, facebook_connections, facebook_unified, goodreads, google_activity, google_contacts, google_drive, google_fit, google_timeline, google_voice, imessage, mbox, onedrive, phone_calls_xml, phone_photos, phone_sms, raindrop, sms_xml, spotify, staged_md, strong, writing_deltas) + 1 stub plugin (readaction).
- **Shared upsert helpers in `phdb.formats/`** — `bookmark_upserts`, `email_upserts`, `chat_upserts`, `conversation_upserts`, `person_upserts` — extracted as plugins ported; mirror the COALESCE last-write-wins pattern.
- **`phdb plugin scaffold <name>`** — generate a skeleton plugin from CLI args; manifest-validates emits against the schemas registry.
- **`phdb plugin list/describe/ingest`** — plugin introspection + ingest CLI.
- **`phdb schema regenerate/diff`** — DB_SCHEMA.md regeneration from the schemas registry + live `sqlite_master`; post-ingest hook (suppress with `--no-schema-regen`).
- **WebPage entity factoring** — `web_pages` is a URL-identity entity table (migration 0023); bookmarks FK into it via `web_page_id`. BrowseAction (migration 0024, apple_dbs) and SearchAction FK retrofit (migration 0025, google_activity) extend the pattern.
- **ReadAction schema + stub plugin** — for future Pocket/Instapaper-shaped reading-list sources (migration 0027).
- **Bookmark triple emission** — `taggedWith` / `inFolder` / `mentions` / `relatesTo` predicates emitted at ingest from raindrop + apple_dbs (migration 0026 seeds the `inFolder` predicate).
- **`docs/plugins.md`** — full author guide (962 lines) covering quick-start, worked example, ABC + manifest reference, shared helpers, testing patterns, discovery + distribution, facet projection, common pitfalls.
- **`phdb-plugin-example`** sibling repo — canonical example third-party plugin demonstrating the contract end-to-end.

### Changed

- `README.md` reframed as plugin-host pitch; first-party plugins listed by domain.
- `phdb.formats/url.py` extracted as the first shared adapter primitive (precedent for the `*_upserts` family).
- Migration numbering passed 0028 (was 0023 pre-Phase-7).

### Plan reference

`Outputs/Plans/phdb Plugin Architecture.md` — 10-phase plan; Phases 1-9 shipped in this release (1 core extraction, 2 schemas pillar, 3 plugin contract, 4 facets framework, 5 raindrop pilot, 6 schema-doc regen, 7 plugin port × 33 briefs + WPEF follow-ons, 9 polish + docs + scaffolder + example). Phase 8 (identity coalescence rules engine) and Phase 10 (final polish + 0.4.0 release) follow this beta.

## [0.3.0] - 2026-05-19

### Added

- **Records-layer architecture** — `Source → Extractor → Format Parser → Typed Record → Vendor Adapter → AdapterRow → DB` pipeline fully realized across all 34 adapters
- **`phdb.formats.*` module** — 15 format parsers extracted from adapters as pure functions yielding frozen dataclass records; no DB or identity dependencies
- **`phdb.records.*` module** — typed record dataclasses (`ChatMessage`, `ChatSession`, `BookmarkEvent`, `Connection`, `MediaPlay`, `ArticleRecord`, `ParsedRecord`, `ParsedWorkout`, `CallRecord`, `WebActivity`, `DigitalDocument`)
- **Sidecar-table API** — `SidecarTableDef` declared on adapter class; base auto-creates tables and auto-inserts child rows via `AdapterRow.sidecar_rows`
- **Core/extras repo split** — 5 Rob-specific adapters moved to `personal-history-extras`; discovered via `phdb.adapters` entry-point group
- **`articles` adapter** — writes to the new `articles` table (migration 0014)
- **Migration 0014** — `articles` table + `commit_authorship` support

### Changed

- All adapters consume typed records from format parsers instead of inline parsing
- `ADAPTERS.md` restructured into Core (29) and Extras (5) sections
- `loader.py` discovers adapters from both `adapter_paths` config and `importlib.metadata` entry points

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

[0.3.0]: https://github.com/robfischer1/personal-history-db/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/robfischer1/personal-history-db/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/robfischer1/personal-history-db/releases/tag/v0.1.0
