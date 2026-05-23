"""PluginManifest — typed parse of a plugin's ``plugin.toml`` file.

Phase 3 deliverable. A plugin's TOML manifest declares everything
phdb needs to know about it without importing its code:

- ``name``, ``version``, ``description`` — identity
- ``kind`` = ``source`` or ``facet`` — discriminator
- ``entry_point`` — the dotted Python path to the plugin class
- ``emits`` (source only) — list of Schema.org @type strings the
  plugin writes into; must resolve against ``phdb.schemas``
- ``entity_refs`` (source only) — list of entity table names this
  plugin's actions FK to
- ``formats_used`` (source only) — list of ``phdb.formats`` modules
  this plugin imports; declarative dependency for audit
- ``records_required`` (source only) — list of ``phdb.records`` types
  this plugin's parser yields
- ``embeddable_tables`` (source only) — list of (table, body_column,
  filter_clause) tuples for the embed pipeline
- ``sidecars`` (source only) — list of sidecar table names
- ``consumes`` (facet only) — facet type this plugin owns
- ``node_table`` (facet only) — the table that holds facet nodes
- ``coalescence_rules_path`` (facet only) — path to the TOML rules
  file (e.g., ``personal-history-instance/identity_rules.toml``)

Format example (source plugin):

    [phdb]
    manifest_version = 1

    [plugin]
    name = "raindrop"
    version = "0.4.0"
    description = "Raindrop.io bookmark ingester"
    kind = "source"
    entry_point = "phdb.plugins.raindrop:RaindropPlugin"

    [source]
    emits = ["BookmarkAction"]
    entity_refs = ["web_pages"]
    formats_used = ["url"]
    records_required = []
    embeddable_tables = []
    sidecars = []
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SourceManifestExtras:
    """Source-plugin-only manifest fields."""

    emits: list[str] = field(default_factory=list)
    entity_refs: list[str] = field(default_factory=list)
    formats_used: list[str] = field(default_factory=list)
    records_required: list[str] = field(default_factory=list)
    embeddable_tables: list[dict[str, str]] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)
    facets_projected: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FacetManifestExtras:
    """Facet-plugin-only manifest fields."""

    consumes: str = ""
    node_table: str = ""
    coalescence_rules_path: str | None = None


@dataclass(frozen=True)
class PluginManifest:
    """Parsed ``plugin.toml`` payload."""

    name: str
    version: str
    description: str
    kind: str  # "source" or "facet"
    entry_point: str
    manifest_version: int = 1
    source: SourceManifestExtras | None = None
    facet: FacetManifestExtras | None = None


def parse_manifest_toml(raw: bytes) -> PluginManifest:
    """Parse ``plugin.toml`` bytes into a PluginManifest."""
    data = tomllib.loads(raw.decode("utf-8"))
    phdb_section = data.get("phdb", {})
    plugin = data.get("plugin", {})

    manifest_version = int(phdb_section.get("manifest_version", 1))
    name = plugin.get("name") or ""
    version = plugin.get("version") or "0.0.0"
    description = plugin.get("description") or ""
    kind = plugin.get("kind") or "source"
    entry_point = plugin.get("entry_point") or ""

    if not name:
        raise ValueError("plugin.toml: [plugin].name is required")
    if not entry_point:
        raise ValueError("plugin.toml: [plugin].entry_point is required")
    if kind not in ("source", "facet"):
        raise ValueError(f"plugin.toml: kind must be 'source' or 'facet'; got {kind!r}")

    source_extras: SourceManifestExtras | None = None
    facet_extras: FacetManifestExtras | None = None

    if kind == "source":
        s = data.get("source", {})
        source_extras = SourceManifestExtras(
            emits=list(s.get("emits", [])),
            entity_refs=list(s.get("entity_refs", [])),
            formats_used=list(s.get("formats_used", [])),
            records_required=list(s.get("records_required", [])),
            embeddable_tables=list(s.get("embeddable_tables", [])),
            sidecars=list(s.get("sidecars", [])),
            facets_projected=list(s.get("facets_projected", [])),
        )
    else:
        f = data.get("facet", {})
        consumes = f.get("consumes")
        node_table = f.get("node_table")
        if not consumes:
            raise ValueError("plugin.toml: facet plugins require [facet].consumes")
        if not node_table:
            raise ValueError("plugin.toml: facet plugins require [facet].node_table")
        facet_extras = FacetManifestExtras(
            consumes=consumes,
            node_table=node_table,
            coalescence_rules_path=f.get("coalescence_rules_path"),
        )

    return PluginManifest(
        name=name,
        version=version,
        description=description,
        kind=kind,
        entry_point=entry_point,
        manifest_version=manifest_version,
        source=source_extras,
        facet=facet_extras,
    )


def load_manifest(path: Path) -> PluginManifest:
    """Load and parse a ``plugin.toml`` from disk."""
    with open(path, "rb") as f:
        return parse_manifest_toml(f.read())


__all__ = [
    "FacetManifestExtras",
    "PluginManifest",
    "SourceManifestExtras",
    "load_manifest",
    "parse_manifest_toml",
]
