# phdb plugins — author guide

phdb is a plugin host. **Source plugins** ingest one data source (Gmail
mbox, Spotify streaming history, Discord exports, …) into typed action
rows. **Facet plugins** subscribe to source emissions and project them
into a facet node graph (Person, Place, Time, Thread, Topic). Both are
discovered via Python entry points; in-tree first-party plugins ship
with the `phdb` distribution under `src/phdb/plugins/<name>/` and
`src/phdb/facets/<name>/`.

This doc is the contract third-party plugin authors target.

> **Status (2026-05-23):** Phase 9 of the phdb Plugin Architecture
> plan. All 34 first-party adapters have ported to the plugin contract
> (Phase 7); five first-party facet plugins ship as skeleton consumers
> (Phase 4); the rules-engine coalescer lands Phase 8. The contract is
> stable as of v0.4.0.

---

## Table of contents

1. [When to write a plugin](#when-to-write-a-plugin)
2. [Quick start](#quick-start)
3. [Worked example — porting a notion-export plugin](#worked-example--porting-a-notion-export-plugin)
4. [Plugin variants](#plugin-variants)
5. [Manifest reference — plugin.toml](#manifest-reference--plugintoml)
6. [ABC reference — runtime contract](#abc-reference--runtime-contract)
7. [Shared helpers in phdb.formats](#shared-helpers-in-phdbformats)
8. [Testing patterns](#testing-patterns)
9. [Discovery and distribution](#discovery-and-distribution)
10. [Facet projection](#facet-projection)
11. [CLI surface](#cli-surface)
12. [Common pitfalls](#common-pitfalls)

---

## When to write a plugin

Before authoring a new plugin, check whether an existing one already
handles your source. Two patterns are common:

| Situation | Action |
| :--- | :--- |
| Your source emits the same `@type` as an existing plugin (e.g. another mail format) | Write a new plugin — multiple sources can co-emit `EmailMessage` (gmail mbox, mbox, apple_dbs all do). |
| Your source is a different shape but same domain (e.g. a different bookmark exporter) | Write a new plugin — re-use shared helpers (`bookmark_upserts`, `email_upserts`, etc.) for the DB-write half. |
| You want to change how an existing plugin parses | Submit a PR — don't fork. The parser modules in `phdb.formats/` are the seam. |
| You want a custom coalescence rule for an existing facet (e.g. People) | Add a TOML rule to `identity_rules.toml`, not a new plugin. |
| You want a new facet type entirely (e.g. `Project`) | Write a facet plugin. |

### Multi-source `@type` pattern

The cleanest example is `EmailMessage`. Three first-party plugins —
`mbox`, `gmail`, `apple_dbs` — all emit `EmailMessage` records into the
`emails` table. Each owns its own discovery and parse logic; all three
funnel through `phdb.formats.email_upserts.upsert_email_message`. Add
the fourth (e.g. an Outlook PST plugin) by following the same shape —
no schema or framework change needed.

---

## Quick start

Scaffold a new plugin from the in-tree template:

```bash
phdb plugin scaffold my_source \
    --emits="Message" \
    --formats-used="myformat" \
    --kind=source
```

This generates the canonical layout under `src/phdb/plugins/my_source/`
(in-tree) or `phdb-plugin-my_source/` (standalone, with `--standalone`):

```
my_source/
├── __init__.py          # re-exports MySourcePlugin
├── plugin.py            # the PhdbSourcePlugin subclass
├── plugin.toml          # the manifest
└── ingest.py            # per-source upsert / triple-emission helpers
```

Edit the generated files, install in editable mode, and verify:

```bash
uv pip install -e .
phdb plugin list           # my_source should appear with [in_tree]
phdb plugin describe my_source
```

Then ingest:

```bash
phdb plugin ingest my_source ~/path/to/source-data
```

Once tests pass and the plugin is stable, ship it (see
[Discovery and distribution](#discovery-and-distribution) below).

---

## Worked example — porting a notion-export plugin

This walks a hypothetical `notion-export` source plugin end to end. Use
the in-tree `raindrop` plugin as the reference shape; the names below
are illustrative.

### Step 1 — claim the `@type` and pick the table

`notion-export` ZIPs contain Markdown pages. Each page is one
`DigitalDocument` row in the existing `documents` table. No schema
change required — `DigitalDocument` and `documents` already exist (see
`src/phdb/schemas/canonical.py`).

If your source needs a brand-new `@type` (e.g. `RecipeAction`), add the
schema and migration first; that's a one-time framework change, not a
plugin change. See `docs/architecture.md` for the schema authoring path.

### Step 2 — write the manifest

`src/phdb/plugins/notion_export/plugin.toml`:

```toml
[phdb]
manifest_version = 1

[plugin]
name = "notion_export"
version = "0.1.0"
description = "Notion workspace export (ZIP of Markdown pages)"
kind = "source"
entry_point = "phdb.plugins.notion_export:NotionExportPlugin"

[source]
emits = ["DigitalDocument"]
entity_refs = []
formats_used = ["notion_md"]
records_required = ["DigitalDocument"]
embeddable_tables = [
    { table = "documents", body_column = "body_text", filter_clause = "schema_type = 'DigitalDocument' AND is_bulk = 0" }
]
sidecars = []
facets_projected = ["Time", "Topic"]
```

Every field is described in the [manifest reference](#manifest-reference--plugintoml).
The `emits` list is validated at load time against
`phdb.schemas.registry` — an `@type` not in the registry raises an
issue on `phdb plugin list`.

### Step 3 — write the parser (`phdb.formats/`)

Format parsers know nothing about the DB. They take a path, yield typed
records from `phdb.records`. Put it in
`src/phdb/formats/notion_md.py`:

```python
"""Parse Notion ZIP exports into DigitalDocument records."""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

from phdb.records import DigitalDocument, Provenance


def parse(source_path: Path) -> Iterator[DigitalDocument]:
    """Yield one DigitalDocument per Markdown page in the export."""
    with zipfile.ZipFile(source_path) as zf:
        for info in zf.infolist():
            if not info.filename.endswith(".md"):
                continue
            body = zf.read(info).decode("utf-8", errors="replace")
            yield DigitalDocument(
                title=Path(info.filename).stem,
                body_text=body,
                provenance=Provenance(
                    source_byte_offset=info.header_offset,
                    source_byte_length=info.file_size,
                    raw_hash=_hash(body),
                ),
            )


def _hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
```

Format parsers are the contract seam — anyone can write a plugin
against a parser without re-implementing the file walking.

### Step 4 — write the ingest helpers (`plugin's ingest.py`)

`src/phdb/plugins/notion_export/ingest.py` owns the per-record DB
write. Reuse shared helpers from `phdb.formats/` wherever possible
(see [Shared helpers](#shared-helpers-in-phdbformats)). For
`DigitalDocument` there's no shared upsert today — write your own
following the canonical pattern:

```python
"""DigitalDocument ingest helpers for the notion-export plugin."""

from __future__ import annotations

import sqlite3

from phdb.records import DigitalDocument


def upsert_document(
    conn: sqlite3.Connection,
    source_file_id: int,
    record: DigitalDocument,
) -> int | None:
    """Insert one document row. Dedups on (source_file_id, raw_hash)."""
    cur = conn.execute(
        """INSERT INTO documents (
            schema_type, title, body_text, body_text_hash,
            raw_hash, source_file_id
        ) VALUES (
            'DigitalDocument', ?, ?, ?, ?, ?
        ) ON CONFLICT(source_file_id, raw_hash) DO NOTHING
        RETURNING id""",
        (record.title, record.body_text, _hash(record.body_text),
         record.provenance.raw_hash, source_file_id),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None
```

Two rules worth burning in:

- **`ingest_row` must be idempotent.** Use `ON CONFLICT … DO NOTHING`
  or `DO UPDATE` in your SQL. A re-run of the same source file with no
  data changes must produce zero new rows. The raindrop plugin's
  `test_idempotent_rerun` is the canonical test.
- **Dedup on `(source_file_id, raw_hash)`.** This is the standard
  shape; every typed table has a `idx_<table>_dedup` unique index over
  these columns. `raw_hash` lives in your `Provenance` dataclass.

### Step 5 — write the plugin class (`plugin.py`)

`src/phdb/plugins/notion_export/plugin.py` is the contract-bearer:

```python
"""NotionExportPlugin — ingests Notion workspace ZIP exports."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.notion_md import parse as parse_notion
from phdb.log import get_logger
from phdb.plugins.notion_export.ingest import upsert_document
from phdb.records import DigitalDocument

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest

log = get_logger("phdb.plugins.notion_export")


@dataclass
class IngestSummary:
    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)


class NotionExportPlugin(PhdbSourcePlugin):
    """Notion workspace export plugin."""

    SOURCE_KIND = "notion-export"
    FILE_KIND = "zip"
    BATCH_SIZE = 500

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        if root.is_file():
            if root.suffix.lower() == ".zip":
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.zip")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[DigitalDocument]:
        yield from parse_notion(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: DigitalDocument,
        *,
        source_file_id: int,
    ) -> int | None:
        return upsert_document(conn, source_file_id, record)

    def register_cli(self, parser: Any) -> None:
        return None  # use `phdb plugin ingest notion_export <path>`

    def register_tools(self, server: Any) -> None:
        return None  # no notion-specific MCP tools yet
```

### Step 6 — write tests

`tests/test_notion_export_plugin.py`:

```python
from pathlib import Path

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.notion_export import NotionExportPlugin

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "notion" / "workspace.zip"


def _new_plugin() -> NotionExportPlugin:
    from phdb.core.plugin.manifest import load_manifest
    manifest = load_manifest(
        Path("src/phdb/plugins/notion_export/plugin.toml").resolve()
    )
    return NotionExportPlugin(manifest)


def test_basic_ingest(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
        plugin = _new_plugin()
        report = plugin.run(FIXTURE_ZIP, conn)
    assert report.rows_inserted > 0


def test_idempotent_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    with connect(db_path) as conn:
        _new_plugin().run(FIXTURE_ZIP, conn)
    with connect(db_path) as conn:
        report = _new_plugin().run(FIXTURE_ZIP, conn)
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    # Second run yields the same records but inserts none new.
    assert report.rows_yielded > 0
    # Total count unchanged after second ingest.
    assert count == report.rows_yielded
```

See [Testing patterns](#testing-patterns) for fixture conventions.

### Step 7 — verify

```bash
uv run pytest tests/test_notion_export_plugin.py -v
phdb plugin list                    # notion_export listed, no issues
phdb plugin describe notion_export  # full manifest dump
phdb plugin ingest notion_export ./sample-export.zip
phdb stats                          # documents count increased
```

That's the whole port. The raindrop plugin under
`src/phdb/plugins/raindrop/` is the real-world equivalent; read it
end-to-end after this walkthrough.

---

## Plugin variants

| Variant | Inherits from | Job |
| :--- | :--- | :--- |
| **Source plugin** | `PhdbSourcePlugin` | Ingest one data source (Gmail, Spotify, Discord, …) into typed action rows |
| **Facet plugin** | `PhdbFacetPlugin` | Subscribe to source emissions and project them into a facet node graph (Person, Place, Time, Thread, Topic) |

Both subclass `PhdbPlugin` and ship with a `plugin.toml` manifest. The
loader treats them identically; the only differences are the ABC
methods each must implement and the manifest sub-table they declare
(`[source]` vs `[facet]`).

---

## Manifest reference — plugin.toml

A plugin's `plugin.toml` declares everything phdb needs to know
without importing the plugin's code. The loader parses every
manifest on startup; broken manifests are surfaced as `issues` on
`phdb plugin list` without preventing the rest of the catalogue from
loading.

### Source plugin example

```toml
[phdb]
manifest_version = 1

[plugin]
name = "raindrop"
version = "0.4.0"
description = "Raindrop.io bookmarks ingester"
kind = "source"
entry_point = "phdb.plugins.raindrop:RaindropPlugin"

[source]
emits = ["BookmarkAction"]
entity_refs = ["web_pages"]
formats_used = ["url", "raindrop"]
records_required = []
embeddable_tables = []
sidecars = []
facets_projected = []
```

### Facet plugin example

```toml
[phdb]
manifest_version = 1

[plugin]
name = "people"
version = "0.4.0"
description = "People facet — Person identity coalescence"
kind = "facet"
entry_point = "phdb.facets.people:PeopleFacetPlugin"

[facet]
consumes = "Person"
node_table = "persons"
coalescence_rules_path = "identity_rules.toml"
```

### `[phdb]` table

| Field | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `manifest_version` | int | `1` | Bump in lockstep with `phdb.core.plugin.MANIFEST_VERSION` when the manifest shape changes. |

### `[plugin]` table (required for all variants)

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `name` | string | yes | Plugin's identifier across CLI / MCP / logs. Lowercase, underscores. |
| `version` | string | no — default `"0.0.0"` | Semver recommended. |
| `description` | string | no | One-line human-readable. |
| `kind` | string | yes | `"source"` or `"facet"`. Anything else raises `ValueError` at parse time. |
| `entry_point` | string | yes | Dotted Python path, `module.path:ClassName`. The loader imports `module.path` and instantiates `ClassName(manifest)`. |

### `[source]` table — source plugins only

Every field is optional; defaults to empty list.

| Field | Type | What it controls |
| :--- | :--- | :--- |
| `emits` | list[string] | Schema.org `@type` strings this plugin writes into typed tables. Validated against `phdb.schemas.registry` at load time. An `@type` not in the registry raises an `issues` entry on the descriptor. |
| `entity_refs` | list[string] | Entity table names this plugin's actions FK to (e.g. `["web_pages"]` for raindrop). Declarative — documents the FK dependency. |
| `formats_used` | list[string] | `phdb.formats` module names this plugin imports. Declarative dependency for audit. |
| `records_required` | list[string] | `phdb.records` types this plugin's parser yields. Documents the producer contract. |
| `embeddable_tables` | list[table] | Each row: `{ table, body_column, filter_clause }`. Tells the embed pipeline which rows of which tables to chunk + embed. |
| `sidecars` | list[string] | Sidecar table names this plugin owns (e.g. `attachments`, `record_metadata`). |
| `facets_projected` | list[string] | Facet types this plugin emits via `project_facets`. Pure documentation today; Phase 8+ may use it for prune-on-uninstall. |

### `[facet]` table — facet plugins only

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `consumes` | string | yes | The facet type this plugin owns (e.g. `"Person"`, `"Place"`, `"Time"`). The `EmissionBus` routes any emission with `facet_type == consumes` to this plugin's `consume()` method. |
| `node_table` | string | yes | The table that holds facet nodes (e.g. `"persons"`). |
| `coalescence_rules_path` | string | no | Relative path (resolved against the instance directory) to the TOML rules file. Phase 8 reads these at `coalesce()` time. |

### Validation behavior

The loader runs `_validate_descriptors` on every parsed manifest:

- For source plugins: each `emits` entry is checked against
  `phdb.schemas.registry.default_schema_registry()`. Unknown types are
  appended to `descriptor.issues` as
  `"emits 'Foo' which is not in phdb.schemas registry"`.
- Manifest parse failures (missing required field, wrong `kind`, etc.)
  are caught per-plugin — the bad plugin gets a placeholder descriptor
  with `issues=["manifest parse failed: …"]`; the rest of the catalogue
  loads cleanly.
- `phdb plugin list` shows issues alongside the plugin name. `phdb
  plugin describe <name>` prints the full issue list.

---

## ABC reference — runtime contract

Plugins inherit from `PhdbSourcePlugin` or `PhdbFacetPlugin`. Missing
`@abstractmethod`s raise `TypeError` at instantiation — runtime
validation per Phase 0 Q4 override (ABCs not Protocols, so we get a
hard fail at plugin load rather than a confusing AttributeError mid-
ingest).

### `PhdbPlugin` (common base)

```python
class PhdbPlugin(ABC):
    manifest: PluginManifest

    def __init__(self, manifest: PluginManifest) -> None:
        self.manifest = manifest

    @property
    def name(self) -> str: ...   # from manifest.name
    @property
    def kind(self) -> str: ...   # from manifest.kind

    @abstractmethod
    def register_cli(self, parser: Any) -> None: ...
    @abstractmethod
    def register_tools(self, server: Any) -> None: ...
```

Both variants must implement `register_cli` and `register_tools`. Most
plugins return `None` from both today — the generic
`phdb plugin ingest <name> <path>` command runs `discover` → `parse` →
`ingest_row` automatically; per-plugin CLI verbs are only needed for
non-standard workflows.

**__init__ contract:** subclasses that override `__init__` must call
`super().__init__(manifest)` first. Missing this raises an
`AttributeError` on first `self.manifest` access.

### `PhdbSourcePlugin`

| Method | When called | What to return |
| :--- | :--- | :--- |
| `discover(root: Path) -> Iterator[(Path, str)]` | At the start of `phdb plugin ingest <name> <root>`. Walks `root` and yields one `(path, source_kind)` per file the plugin can ingest. | Must be a generator — do not eagerly materialize. Empty yield is fine (signals "nothing to ingest here"). |
| `parse(path: Path) -> Iterator[Any]` | Once per file from `discover`. Yields typed records from `phdb.records`. | Must be a generator. Records get fed one-by-one into `ingest_row`. |
| `ingest_row(conn, record, *, source_file_id, ...) -> int \| None` | Once per record from `parse`. Persists the record to its typed table. | Returns the inserted row id; `None` means the record was a duplicate (already in DB). Must be idempotent. |
| `project_facets(emission_bus, record) -> None` | **Optional** (default no-op). Called once per record after `ingest_row` if the plugin wants to feed facets. | Emit `FacetEmission` events via `emission_bus.emit(...)`. |
| `register_cli(parser) -> None` | At CLI startup. | Add plugin-specific subcommands to `parser`. Default: no-op. |
| `register_tools(server) -> None` | At MCP server startup. | Register plugin-specific MCP tools on the FastMCP `server`. Default: no-op. |

Most plugins also expose a convenience `run(source_path, conn,
settings)` method — not part of the ABC, but the standard shape used
by the raindrop / mbox / spotify exemplars. `phdb plugin ingest`
currently invokes `run()` when present; in Phase 10 this will move
fully behind a framework runner.

### `PhdbFacetPlugin`

| Method | When called | What to return |
| :--- | :--- | :--- |
| `consume(emission: FacetEmission) -> None` | Once per emission whose `facet_type == manifest.facet.consumes`. Dispatched by the `EmissionBus`. | Plugin decides — buffer in memory, write to staging table, write directly to facet node table. Phase 4 `SkeletonFacetPlugin` just buffers. |
| `coalesce() -> dict` | On `phdb facet <name> coalesce`. Runs identity coalescence over accumulated emissions. | A summary dict — at minimum `{"buffered_emissions": N, "facet_type": ..., "node_table": ...}`. Phase 8 adds rules-engine outputs. |

### Common pitfalls in ABC implementations

- **`ingest_row` not idempotent.** Re-running a source file produces
  duplicate rows. Fix: always `ON CONFLICT(source_file_id, raw_hash)
  DO NOTHING` or `DO UPDATE`. Test it: write `test_idempotent_rerun`.
- **`discover` returns a `list` instead of yielding.** Type checker
  doesn't catch it; runtime works but holds the whole walk in memory
  for huge directories. Use `yield` / `yield from`.
- **Forgetting `super().__init__(manifest)`** in `__init__`. The base
  class stores the manifest; without it, `self.name` and
  `self.manifest` are missing.
- **Mutating shared state in `parse`.** Multiple files in `discover`
  call `parse` in sequence; per-file state belongs in `parse`'s
  locals, not on `self`.

---

## Shared helpers in `phdb.formats`

`phdb.formats/` is the shared substrate for parsers + upserts. Two
classes of module live there:

### Format parsers (one per file format)

Pure parsers — take a path, yield typed records, no DB knowledge. One
per source format. Examples: `mbox.py`, `spotify_json.py`,
`raindrop.py`, `apple_health_xml.py`, `facebook_html.py`. If your
plugin parses a format another plugin also reads, share the parser.

### Shared upsert modules (one per `@type` family)

These hold the DB-write half — the typed table's INSERT/UPDATE/dedup
logic — so multiple plugins can write into the same typed table without
duplicating SQL.

| Module | What it upserts | Used by |
| :--- | :--- | :--- |
| `bookmark_upserts.py` | `WebPage` entity + `BookmarkAction` + bookmark↔web_page triples | raindrop, apple_dbs |
| `email_upserts.py` | `EmailMessage` + sidecars (attachments, recipients) | mbox, apple_dbs |
| `chat_upserts.py` | `ChatMessage` rows into `chat_messages` | discord, imessage, phone_sms, sms_xml, google_voice, chat_logs, apple_dbs, facebook_unified |
| `conversation_upserts.py` | `Conversation` rows (AI-session messages) into `conversations_messages` | claude_chat, claude_code |
| `person_upserts.py` | `Person` action rows into `persons` table | google_contacts, facebook_connections |

**When to reuse vs. write your own:**

- **Reuse** if your plugin's records map cleanly to an existing
  typed table. Reuse keeps dedup semantics consistent across all
  sources of that `@type` — important for downstream facet
  coalescence.
- **Write your own helper** in your plugin's `ingest.py` only when
  your table doesn't have a shared module yet (e.g.
  `DigitalDocument` → `documents` today). Drop the helper in
  `phdb/formats/<type>_upserts.py` if a second plugin starts needing
  it.

---

## Testing patterns

### Fixture conventions

Per-plugin fixtures live at `tests/fixtures/<plugin_name>/`. Keep
them synthetic — every row is hand-authored or generator-produced;
never check in real personal data. Many adapters ship a
`generate_<thing>.py` next to the binary fixture for reproducibility
(see `tests/fixtures/generate_mbox.py`, `generate_discord_zip.py`,
`generate_strong_db.py`).

```
tests/fixtures/
├── raindrop/
│   └── raindrop_export.csv         # 5 hand-authored rows
├── discord/
│   ├── package.zip                 # built by generate_discord_zip.py
│   └── ...
└── apple_health/
    └── Health_Export.zip
```

### Per-test DB setup with `tmp_path` + `MigrationRunner`

Every test gets its own fresh SQLite DB via pytest's `tmp_path`:

```python
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner

def _setup(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    return db_path
```

Or use the shared fixtures from `tests/conftest.py`:

| Fixture | Returns | Use when |
| :--- | :--- | :--- |
| `tmp_db` | `Path` to a fresh DB file (not yet created) | You want to create + migrate manually. |
| `migrated_db` | `Path` to a DB with all migrations applied | You want a ready-to-write DB. |
| `test_settings` | `Settings` pointing at `tmp_db` | You need a `Settings` object (e.g. for direction inference). |
| `test_identity` | `IdentitySettings` with synthetic owner | You need to test direction-inference rules. |
| `instance_dir` | `Path` to a synthetic instance dir with `identity.toml` + `embedding.toml` | You need a real instance directory on disk. |

### Building the plugin in tests

Load the manifest from disk so the manifest validation runs end-to-end:

```python
def _new_plugin() -> NotionExportPlugin:
    from phdb.core.plugin.manifest import load_manifest
    manifest = load_manifest(
        Path("src/phdb/plugins/notion_export/plugin.toml").resolve()
    )
    return NotionExportPlugin(manifest)
```

### Unit vs. integration tests

| Test kind | What it covers | Where |
| :--- | :--- | :--- |
| **Unit** | One pure function in your `phdb.formats/<x>.py` parser (URL normalization, junk detection, etc.). | `tests/test_<plugin>_adapter.py` — small classes per concern (`TestUrlNormalization`, `TestJunkDetection`). |
| **Integration** | The plugin's `run()` method against a fixture, asserting on the resulting DB rows. | Same file — `TestRaindropPluginIngest` etc. Always include `test_basic_ingest` + `test_idempotent_rerun`. |

The raindrop test file (`tests/test_raindrop_adapter.py`) is the
canonical exemplar; mirror its layout.

---

## Discovery and distribution

phdb discovers plugins from two sources:

### In-tree plugins (first-party)

Live at `src/phdb/plugins/<name>/` (sources) and
`src/phdb/facets/<name>/` (facets). The loader walks each root
directory at startup, parses every `plugin.toml`, and instantiates
the plugin class.

In-tree plugins don't need an entry-point declaration — the directory
walk is enough.

### Entry-point plugins (pip-installable, third-party)

Standalone distributions declare their plugin via the
`phdb.plugins` (source) or `phdb.facets` (facet) entry-point group in
`pyproject.toml`:

```toml
[project.entry-points."phdb.plugins"]
notion_export = "phdb_plugin_notion_export:NotionExportPlugin"

[project.entry-points."phdb.facets"]
projects = "phdb_facet_projects:ProjectsFacetPlugin"
```

Once `pip install phdb-plugin-notion-export` runs, the plugin appears
under `phdb plugin list` without any phdb-side code change.

Entry-point plugins ship their `plugin.toml` as package data —
typically at `src/phdb_plugin_<name>/plugin.toml`. The loader reads
it via `importlib.resources`, falling back gracefully if the
manifest is missing.

### Naming conventions

| Layer | Convention | Example |
| :--- | :--- | :--- |
| Distribution name on PyPI | `phdb-plugin-<name>` (source) or `phdb-facet-<name>` (facet) | `phdb-plugin-notion-export` |
| Python module | `phdb_plugin_<name>` / `phdb_facet_<name>` | `phdb_plugin_notion_export` |
| Plugin class | `<Name>Plugin` (PascalCase, `Plugin` suffix) | `NotionExportPlugin` |
| `[plugin].name` | matches distribution name minus `phdb-plugin-` prefix; lowercase, underscores | `notion_export` |

The `[plugin].name` field is what users type — `phdb plugin ingest
notion_export ./path`. Keep it short and unambiguous.

### Conflict resolution

If both an in-tree plugin and an entry-point plugin claim the same
`[plugin].name`, the entry-point wins (pip-installed package is
considered more authoritative than the in-tree shadow). To override
a first-party plugin, ship a third-party plugin with the same
`[plugin].name`.

---

## Facet projection

Source plugins can emit facet projections at ingest time via the
`EmissionBus`. The bus is in-process and synchronous; persistence
happens per-facet in the audit log (`facet_coalescence_log`).

> **Status:** `project_facets` is an opt-in hook with a default no-op
> on `PhdbSourcePlugin`. The Phase 4 `SkeletonFacetPlugin`
> implementations buffer emissions; the full rules-engine coalescer
> lands in Phase 8. Plugins should emit normally now — Phase 8
> consumes the emissions retroactively.

### Emitting from a source plugin

```python
from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.plugin.bus import EmissionBus

class MyPlugin(PhdbSourcePlugin):
    # ... discover / parse / ingest_row as usual ...

    def project_facets(
        self,
        emission_bus: EmissionBus,
        record: MyRecord,
    ) -> None:
        # Person emission
        if record.sender_address:
            emission_bus.emit(
                source_table="chat_messages",
                source_id=record.id,
                facet_type="Person",
                payload={
                    "email": record.sender_address,
                    "display_name": record.sender_name,
                    "source_address": record.sender_address,
                },
            )

        # Time emission
        if record.date_sent:
            emission_bus.emit(
                source_table="chat_messages",
                source_id=record.id,
                facet_type="Time",
                payload={"timestamp": record.date_sent},
            )
```

Declare which facet types you emit in your manifest:

```toml
[source]
facets_projected = ["Person", "Time"]
```

### The five first-party facet plugins

All five ship as Phase 4 skeletons (buffer-only) in
`src/phdb/facets/<name>/`:

| Plugin | Consumes | Node table | Coalescence rules path |
| :--- | :--- | :--- | :--- |
| `people` | `Person` | `persons` | `identity_rules.toml` |
| `places` | `Place` | `places` | (TBD Phase 8) |
| `time` | `Time` | (time index) | (none) |
| `threads` | `Thread` | `nodes` (kind='thread') | (none) |
| `topics` | `Topic` | (TBD Phase 8) | (TBD Phase 8) |

Phase 8 replaces `SkeletonFacetPlugin` with the rules-engine
implementation; the contract above stays.

---

## CLI surface

```text
phdb plugin list                      # list installed source + facet plugins
phdb plugin describe <name>           # show full manifest + issues for a plugin
phdb plugin ingest <name> <path>      # run discover → parse → ingest_row
phdb plugin scaffold <name>           # generate the in-tree plugin layout
```

The `phdb plugin scaffold` command is the recommended starting point
— it generates the canonical layout (manifest + plugin.py + ingest.py
+ `__init__.py`) so you only fill in the source-specific logic.

Per-plugin CLI subcommands (registered via `register_cli`) land under
`phdb <plugin-name> ...` at startup; today most plugins return `None`
from `register_cli` and rely on the generic `phdb plugin ingest`
runner.

---

## Common pitfalls

Drawn from the 34 Phase 7 plugin ports. Most of these will catch
you exactly once.

### Bytes-literal in SQL with non-ASCII chars

```python
# WRONG — Python rejects bytes literals containing non-ASCII characters
conn.execute(b"SELECT 'café'")           # SyntaxError

# RIGHT — keep SQL as str, encode only at the transport layer if needed
conn.execute("SELECT 'café'")
```

Sqlite3's `Connection.execute` takes `str`. The bytes-literal
restriction caught two ports during Phase 7 when copy-pasted SQL
included a smart-quote.

### Migration registration footer

Every new migration in `src/phdb/migrations/project/<NNNN>_<name>.sql`
auto-registers via the directory walk — *no manual footer needed*.
The Phase 7 ports that hit this had stale lingering scaffolding from
the pre-migration-runner era.

### ABC `__init__` contract

```python
# WRONG — manifest never gets stored
class MyPlugin(PhdbSourcePlugin):
    def __init__(self, manifest, *, custom_arg=None):
        self.custom_arg = custom_arg
        # missing super().__init__(manifest)

# RIGHT
class MyPlugin(PhdbSourcePlugin):
    def __init__(self, manifest, *, custom_arg=None):
        super().__init__(manifest)
        self.custom_arg = custom_arg
```

`PhdbPlugin.__init__` assigns `self.manifest = manifest`. Skip it and
`self.name` raises `AttributeError`.

### Fixture sharing across plugins

Don't reach into another plugin's fixture directory. Each plugin owns
`tests/fixtures/<its_name>/`. If two plugins need the same fixture
shape, factor it into a generator in `tests/fixtures/generate_<x>.py`
and have each plugin's test build its own copy in `tmp_path`.

### `ingest_row` returning the wrong type

The contract says `int`. Some shared upsert helpers return `int |
None` (None = duplicate). If your `ingest_row` calls one of these,
its signature should be `-> int | None` and `run()` must treat `None`
as a skip:

```python
msg_id = self.ingest_row(conn, record, source_file_id=sf_id)
if msg_id is None:
    report.rows_skipped += 1
    continue
report.rows_inserted += 1
```

### Historical: sentinel-rotation patterns

Pre-Phase-7 adapters used module-level sentinel objects to thread
state through `iter_rows`. The new contract makes per-record state
local to `parse` / `ingest_row`; the sentinel pattern is no longer
needed. If you're porting an old adapter, drop the sentinel and pass
state via dataclasses on the records themselves.

### Forgetting to commit

`run()` should `conn.commit()` periodically (every `BATCH_SIZE`
records) plus once at the end. Without intermediate commits, a
crashed ingest of a 6M-row Apple Health export loses everything.
Apple Health uses `COMMIT_EVERY = 25000`; raindrop uses
`BATCH_SIZE = 500`. Pick a size that matches your record's write
cost.

### `discover` returning the source path even when it's wrong

```python
# WRONG — yields any CSV under root, not just raindrop CSVs
for path in sorted(root.rglob("*.csv")):
    yield path, self.SOURCE_KIND

# RIGHT for plugins that need filename discipline
for path in sorted(root.rglob("Streaming_History_*.json")):
    yield path, self.SOURCE_KIND
```

If your source format is identifiable by filename pattern, use it.
Generic globs (`*.csv`, `*.json`) work for single-purpose directories
but break when the user points `discover` at a directory containing
mixed sources.

---

## Further reading

- [docs/architecture.md](architecture.md) — 4-layer architecture
  (core / schemas / facets / plugins), DB schema, migration system.
- [src/phdb/plugins/raindrop/](../src/phdb/plugins/raindrop/) — Phase 5
  pilot plugin; canonical reference.
- [src/phdb/plugins/mbox/](../src/phdb/plugins/mbox/) — multi-record
  emission (EmailMessage + Attachment + Recipient).
- [src/phdb/plugins/apple_health/](../src/phdb/plugins/apple_health/) —
  largest plugin; streaming XML; multiple sidecar tables.
- [src/phdb/plugins/spotify/](../src/phdb/plugins/spotify/) — simplest
  Phase 7 port; single typed table, no sidecars.
- [src/phdb/core/plugin/contract.py](../src/phdb/core/plugin/contract.py)
  — ABC spec.
- [src/phdb/core/plugin/manifest.py](../src/phdb/core/plugin/manifest.py)
  — manifest parser.
- [src/phdb/core/plugin/bus.py](../src/phdb/core/plugin/bus.py) —
  `EmissionBus` + `FacetEmission`.
