"""phdb.core.plugin — plugin contract, manifest, and loader.

Phase 3 deliverable of the phdb Plugin Architecture plan. Exposes the
PhdbPlugin ABC family + PluginManifest dataclass + the entry-point-
based loader. Two plugin variants share the package:

- ``PhdbSourcePlugin`` (kind: ``source``) — ingests a data source and
  emits action rows into typed tables defined by ``phdb.schemas``.
  Declares ``emits = ["EmailMessage", "Observation"]`` etc. in its
  manifest.
- ``PhdbFacetPlugin`` (kind: ``facet``) — subscribes to source
  emissions and projects them into a facet node graph (Person, Place,
  Time, Thread, Topic). Declares ``consumes = "<facet-type>"``.

The legacy ``phdb.core.plugin_loader`` module re-exports the loader
surface from here for backward compatibility with the Phase 1 scaffold.
"""

from __future__ import annotations

from phdb.core.plugin.contract import (
    PhdbFacetPlugin,
    PhdbPlugin,
    PhdbSourcePlugin,
)
from phdb.core.plugin.loader import (
    ENTRY_POINT_GROUP,
    FACET_ENTRY_POINT_GROUP,
    PluginDescriptor,
    discover_facets,
    discover_plugins,
    load_plugin,
    validate_plugin,
)
from phdb.core.plugin.manifest import (
    FacetManifestExtras,
    PluginManifest,
    SourceManifestExtras,
    load_manifest,
    parse_manifest_toml,
)

MANIFEST_VERSION = 1

__all__ = [
    "ENTRY_POINT_GROUP",
    "FACET_ENTRY_POINT_GROUP",
    "FacetManifestExtras",
    "MANIFEST_VERSION",
    "PhdbFacetPlugin",
    "PhdbPlugin",
    "PhdbSourcePlugin",
    "PluginDescriptor",
    "PluginManifest",
    "SourceManifestExtras",
    "discover_facets",
    "discover_plugins",
    "load_manifest",
    "load_plugin",
    "parse_manifest_toml",
    "validate_plugin",
]
