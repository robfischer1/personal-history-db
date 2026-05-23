# Migrating from v0.3 to v0.4

phdb 0.4 is a **hard-break refactor**. The Q14 decision (no shim layer)
locked in at planning time means there is no transition release — the
v0.3 adapter API and its CLI surface are gone. This guide walks the
changes you need to make if you have a v0.3 install.

If you are starting fresh on v0.4 and never used v0.3, skip this guide
and read [`docs/plugins.md`](plugins.md) instead.

---

## TL;DR

| v0.3 | v0.4 |
| :--- | :--- |
| `from phdb.adapters import RaindropAdapter` | `from phdb.plugins.raindrop import RaindropPlugin` |
| `phdb ingest path/to/file --adapter raindrop` | `phdb ingest path/to/file --plugin raindrop` |
| `Adapter` ABC + `register_tools()` | `PhdbSourcePlugin` ABC + `plugin.toml` manifest + entry-point discovery |
| `bookmarks.url` / `.title` / `.normalized_url` columns | JOIN `bookmarks` to `web_pages` on `web_page_id` |
| Coalescence was a Phase-4 skeleton no-op | TOML-rules engine; `phdb facet people review` |
| `pip install personal-history-db` | `pip install personal-history-db[server]` (if you used the MCP server) |

---

## 1. Plugin layout replaces adapter package

In v0.3, all ingesters lived in `src/phdb/adapters/` as `Adapter` subclasses
discovered by name. In v0.4, each source is a self-contained plugin
package under `src/phdb/plugins/<name>/` with:

```
src/phdb/plugins/raindrop/
├── __init__.py        # exports RaindropPlugin
├── plugin.toml        # PluginManifest declaration
├── plugin.py          # PhdbSourcePlugin subclass
└── ...                # source-specific helpers
```

Discovery happens via Python entry points (`phdb.plugins` group) at
package install time. First-party plugins are also discovered via an
in-tree scan so a checkout works without `pip install -e .`.

### Action

Update any imports:

```python
# v0.3
from phdb.adapters import RaindropAdapter
adapter = RaindropAdapter()

# v0.4
from phdb.plugins.raindrop import RaindropPlugin
plugin = RaindropPlugin(manifest)  # see plugins.md for manifest loading
```

If you authored third-party adapters: see [`docs/plugins.md §Authoring a plugin`](plugins.md)
for the new structure and the `phdb-plugin-example/` sibling repo for a
runnable scaffold.

---

## 2. CLI flag rename: `--adapter` → `--plugin`

```bash
# v0.3
phdb ingest path/to/file --adapter raindrop

# v0.4
phdb ingest path/to/file --plugin raindrop
```

The old flag will exit with `unrecognized arguments: --adapter`. No
aliasing — by design.

### New plugin CLI surface

```bash
phdb plugin list                # discover + validate all plugins
phdb plugin info <name>         # manifest + declared schemas
phdb facet <name> review        # interactive coalescence proposal review
phdb facet <name> unmerge <id>  # reverse a merge
phdb facets stats               # audit-log summary by facet/rule/confidence
```

---

## 3. Bookmarks table reshape (migration 0028)

The WebPage entity factoring (migration 0023) split bookmarks into two
tables: the canonical `web_pages` entity (URL identity + page metadata)
and `bookmarks` (per-instrument actions on that page). Migration 0028
finishes the job by dropping the duplicated columns from `bookmarks`:

```
DROPPED from bookmarks:  url, normalized_url, title, excerpt, cover_url
REPLACED unique index:   (normalized_url, instrument) → (web_page_id, instrument)
```

If you have queries hitting `bookmarks.url`, `bookmarks.title`, etc.
directly, they will fail. JOIN to `web_pages` instead:

```sql
-- v0.3
SELECT url, title, folder
FROM bookmarks
WHERE instrument = 'raindrop';

-- v0.4
SELECT wp.url, wp.title, b.folder
FROM bookmarks b
JOIN web_pages wp ON wp.id = b.web_page_id
WHERE b.instrument = 'raindrop';
```

The same JOIN-via-FK pattern applies to any new entity refactoring
plugins do. See `docs/plugins.md` for the WebPage Entity Factoring
precedent.

---

## 4. Identity coalescence is real now

In v0.3, the Person / Place / Time / Thread / Topic facets existed
as Phase-4 skeletons — they consumed emissions and buffered them, but
`coalesce()` was a no-op. In v0.4 (Phase 8), the people and places
facets ship working coalescers backed by TOML rules:

```toml
# personal-history-instance/people_rules.toml
[[rules]]
name = "exact_email"
predicate = "email_exact"
confidence = 0.95

[[rules]]
name = "phone_match"
predicate = "phone_e164_exact"
confidence = 0.9

[[rules]]
name = "name_plus_domain"
predicate = "name_and_email_domain"
confidence = 0.7
```

When the people facet ingests an emission, the rules engine evaluates
each rule. Predicates above the `confidence ≥ 0.85` auto-merge threshold
apply immediately and write to `facet_coalescence_log`; lower-confidence
matches go to a JSONL pending-review queue at
`personal-history-instance/facet_coalescence_pending/<facet>.jsonl` for
later interactive review via `phdb facet <facet> review`.

Time / threads / topics remain skeleton — Phase 8 finished people +
places only; the other three will adopt the same `_coalescence_lib`
primitives in a future release.

### Action

If you were patching the v0.3 `coalesce()` stub to do anything custom,
remove that patch — your override won't be called. Replace with a TOML
rules file shipped via the plugin's `coalescence_rules_path` manifest
field. See [`docs/plugins.md §Facet plugins`](plugins.md) for the rule
predicate vocabulary and how to author custom predicates.

---

## 5. Optional dependency split: server extra

The MCP server (FastMCP + the 12 query tools) is now an optional extra:

```bash
# v0.3 — bundled
pip install personal-history-db

# v0.4 — query CLI only
pip install personal-history-db

# v0.4 — with MCP server
pip install personal-history-db[server]
```

If you were running `phdb-mcp-server` and got `ModuleNotFoundError: mcp`,
install with the `[server]` extra. The `phdb` CLI itself has no extra
dependencies.

---

## 6. Migration sequence + DB upgrade path

v0.4 introduces migrations 0023 through 0029. They are all forward-only
SQL files in `src/phdb/migrations/project/`:

| # | Title | Phase |
| :--- | :--- | :--- |
| 0023 | WebPage entity factoring (web_pages table + bookmarks.web_page_id FK) | 5 |
| 0024 | browse_actions table | 5 |
| 0025 | (renumbered batch) | 7 |
| 0026 | (renumbered batch) | 7 |
| 0027 | bookmarks tag triple emission columns | 7 |
| 0028 | drop deprecated bookmark columns (url/title/etc.) | 7 |
| 0029 | formal facet_coalescence_log table | 8 |

The migrator runs them in order on next `phdb` invocation. There is
**no rollback path** through 0023 once 0028 lands — the dropped
columns are gone. Back up your DB before upgrading.

Manual rescue scripts that used to live alongside the forward
migrations now live in `scripts/migrations-rollback/`. They are
documented as one-off emergency tools, not part of the supported
migration sequence.

---

## 7. What didn't change

The big load-bearing things stayed the same so you don't have to
rewrite everything:

- **DB file format** — same SQLite file. Just newer schema.
- **Embedding tables + scoring queries** — `messages`, `sources`,
  `embeddings`, `vec_messages`, `vec_chunks`. The query CLI surface
  (`phdb query`, `phdb similar`, etc.) is unchanged.
- **MCP tool names** — `phdb_search`, `phdb_neighbors`, etc. Same
  names, same payloads. Tools didn't get renamed in the refactor.
- **Schemas registry contents** — the 33 canonical typed tables
  (WebPage, BookmarkAction, EmailMessage, ChatMessage, …) keep their
  Schema.org keys and column shapes. Adding a new schema still works
  the same way (declare on a subclass of `EntitySchema` /
  `ActionSchema` and register it).
- **Instance config** — `personal-history-instance/` still holds the
  DB, ingestion lockfiles, and per-source state. No reshape needed.

---

## 8. If you authored third-party adapters

Sorry — there is no shim. You need to port. The good news: a v0.3
adapter is usually ~70% of a v0.4 plugin. The mapping is mechanical:

| v0.3 adapter method | v0.4 plugin method | Notes |
| :--- | :--- | :--- |
| `discover(root)` | `discover(root)` | unchanged signature |
| `parse(path)` | `parse(path)` | unchanged signature |
| `ingest_row(conn, record)` | `ingest_row(conn, record, **kwargs)` | return type loosened to `int \| None`; `**kwargs` for forward compat |
| `register_tools(server)` | `register_tools(server)` | unchanged |
| `register_cli(parser)` | `register_cli(parser)` | unchanged |
| _(missing)_ | `plugin.toml` manifest | required — see plugins.md |
| _(missing)_ | `project_facets(emission_bus, record)` | optional — for emitting facet identities |
| _(class-level)_ `name`, `version` | `manifest.name`, `manifest.version` | now via the manifest dataclass |

The [`phdb-plugin-example/`](https://github.com/robfischer1/phdb-plugin-example)
sibling repo is a minimal runnable plugin built in v0.4 layout —
copy-paste-rename to start a new third-party plugin.

---

## 9. Where to ask if you get stuck

- New plugin architecture overview: [`README.md`](../README.md)
- Plugin authoring guide: [`docs/plugins.md`](plugins.md)
- Architecture map: [`docs/architecture.md`](architecture.md)
- Contributing: [`CONTRIBUTING.md`](../CONTRIBUTING.md)
- File an issue with your v0.3 → v0.4 sticking point.
