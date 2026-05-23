# phdb plugins

phdb hosts sources and facets as plugins discovered via Python entry
points. This doc is the contract third-party plugin authors target.

> **Status (2026-05-22):** Phase 3 of the phdb Plugin Architecture
> plan. The contract is locked but no first-party plugin has ported
> yet — raindrop is the Phase 5 pilot. This skeleton expands into a
> full author guide during Phase 9 (public-tool polish).

## Plugin variants

| Variant | Inherits from | Job |
| :--- | :--- | :--- |
| **Source plugin** | `PhdbSourcePlugin` | Ingest one data source (Gmail, Spotify, Discord, …) into typed action rows |
| **Facet plugin** | `PhdbFacetPlugin` | Subscribe to source emissions and project them into a facet node graph (Person, Place, Time, Thread, Topic) |

Both subclass `PhdbPlugin` and ship with a `plugin.toml` manifest.

## plugin.toml — the manifest

A plugin's `plugin.toml` declares everything phdb needs to know without
importing the plugin's code.

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
formats_used = ["url"]
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

### Field reference

`[plugin]` — required for both variants:

- `name` (string, required) — used as the plugin's identifier across
  CLI / MCP / logs.
- `version` (string, default `"0.0.0"`) — plugin version.
- `description` (string) — one-line human-readable.
- `kind` (string, required) — `"source"` or `"facet"`.
- `entry_point` (string, required) — dotted Python path,
  `module.path:ClassName`.

`[source]` — only for source plugins. Every field is optional; defaults
to empty:

- `emits` — list of Schema.org `@type` strings; must resolve in
  `phdb.schemas` (validated at load time).
- `entity_refs` — list of entity table names this plugin's actions FK
  to (e.g. `["web_pages"]` for raindrop).
- `formats_used` — list of `phdb.formats` module names this plugin
  imports (`url`, `email_address`, …); declarative dependency.
- `records_required` — list of `phdb.records` types this plugin's
  parser yields.
- `embeddable_tables` — list of `{table, body_column, filter_clause}`
  tables to wire into the embed pipeline.
- `sidecars` — list of sidecar table names this plugin owns.
- `facets_projected` — list of facet types this plugin emits into.

`[facet]` — only for facet plugins:

- `consumes` (required) — the facet type this plugin owns.
- `node_table` (required) — the table that holds facet nodes.
- `coalescence_rules_path` — relative path to the TOML rules file in
  the instance directory.

## ABCs — the runtime contract

Plugins inherit from `PhdbSourcePlugin` or `PhdbFacetPlugin`. Missing
`@abstractmethod`s raise `TypeError` at instantiation — runtime
validation per Phase 0 Q4 override.

### Source plugin contract

```python
from pathlib import Path
from phdb.core.plugin import PhdbSourcePlugin

class RaindropPlugin(PhdbSourcePlugin):
    def discover(self, root: Path):
        # yield (path, source_kind) tuples for every file this plugin handles
        ...

    def parse(self, path: Path):
        # yield typed records from one source file
        ...

    def ingest_row(self, conn, record) -> int:
        # persist one record to its typed table; return the row id
        ...

    def register_cli(self, parser):
        # add the plugin's subcommands to a click/argparse group
        ...

    def register_tools(self, server):
        # register the plugin's MCP tools on a FastMCP server
        ...

    # Optional — default no-op:
    # def project_facets(self, emission_bus, record): ...
```

### Facet plugin contract

```python
from phdb.core.plugin import PhdbFacetPlugin

class PeopleFacetPlugin(PhdbFacetPlugin):
    def consume(self, emission):
        # receive a FacetEmission from a source plugin's project_facets call
        ...

    def coalesce(self) -> dict:
        # run identity coalescence; return summary
        ...

    def register_cli(self, parser): ...
    def register_tools(self, server): ...
```

## Distribution

First-party plugins ship in the main `phdb` distribution under
`src/phdb/plugins/<name>/`. Third-party plugins ship as standalone
distributions named `phdb-plugin-<name>` (or `phdb-facet-<name>`) and
register an entry point in their `pyproject.toml`:

```toml
[project.entry-points."phdb.plugins"]
my_source = "phdb_plugin_my_source:MySourcePlugin"

[project.entry-points."phdb.facets"]
my_facet = "phdb_facet_my_facet:MyFacetPlugin"
```

phdb discovers both via the same loader; in-tree plugins use the bare
plugin name, third-party plugins use whatever name they register.

## CLI

```text
phdb plugin list             # list installed source + facet plugins
phdb plugin describe <name>  # show full manifest for a named plugin
```

## Roadmap

This document is the Phase 3 skeleton — the contract is stable. Phase
9 (public-tool polish) extends it with:

- a worked-example plugin walked end-to-end (scaffold → manifest →
  ABC implementation → tests → CLI invocation)
- `phdb plugin scaffold <name>` invocation walk-through
- `phdb plugin docs <name>` auto-generated README example
- a published example third-party plugin in a sibling repo
