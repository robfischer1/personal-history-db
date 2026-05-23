"""Plugin loader — entry-point discovery + manifest validation + dep check.

Phase 3 deliverable. Walks the ``phdb.plugins`` entry-point group +
the in-tree ``src/phdb/plugins/<name>/`` directory, parses each
plugin's ``plugin.toml`` manifest, validates declared emissions
against the ``phdb.schemas`` registry, and returns a list of
``PluginDescriptor`` records ready for use by CLI / MCP server
aggregation.

Phase 4 extends this to handle the facets entry-point group via the
same machinery; Phase 5+ wires actual plugin instantiation through
``load_plugin`` once raindrop ports.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin.manifest import PluginManifest, load_manifest

if TYPE_CHECKING:
    from phdb.core.plugin.contract import PhdbPlugin

ENTRY_POINT_GROUP = "phdb.plugins"
FACET_ENTRY_POINT_GROUP = "phdb.facets"

# In-tree plugin discovery roots — checked in this order.
_IN_TREE_SOURCE_ROOTS = ["phdb.plugins"]
_IN_TREE_FACET_ROOTS = ["phdb.facets"]


@dataclass(frozen=True)
class PluginDescriptor:
    """Plugin record returned by ``discover_plugins``.

    Carries the manifest + entry-point location; calling ``load_plugin``
    on a descriptor imports the module and returns an instantiated
    plugin (Phase 5+).
    """

    name: str
    distribution: str | None
    entry_point_value: str
    manifest: PluginManifest
    source: str = "entry_point"  # "entry_point" or "in_tree"
    issues: list[str] = field(default_factory=list)


def _entry_point_descriptors(group: str) -> list[PluginDescriptor]:
    out: list[PluginDescriptor] = []
    try:
        eps = entry_points(group=group)
    except TypeError:
        eps = entry_points().get(group, [])
    for ep in eps:
        try:
            dist_name = ep.dist.name if ep.dist else None
        except AttributeError:
            dist_name = None
        # Entry-point-installed plugins ship their plugin.toml in
        # package data; the consumer can find it via importlib.resources.
        manifest = _read_entry_point_manifest(ep)
        if manifest is None:
            continue
        out.append(PluginDescriptor(
            name=ep.name,
            distribution=dist_name,
            entry_point_value=ep.value,
            manifest=manifest,
            source="entry_point",
        ))
    return out


def _read_entry_point_manifest(ep: Any) -> PluginManifest | None:
    """Read plugin.toml for an entry-point plugin via importlib.resources."""
    try:
        from importlib.resources import files  # noqa: PLC0415

        # ep.value is "phdb.plugins.raindrop:RaindropPlugin"; module is the part before ":"
        module_path = ep.value.split(":", 1)[0]
        package = ".".join(module_path.split(".")[:-1]) if "." in module_path else module_path
        if not package:
            return None
        resource_root = files(package)
        manifest_path = resource_root / "plugin.toml"
        if not manifest_path.is_file():
            return None
        from phdb.core.plugin.manifest import parse_manifest_toml  # noqa: PLC0415
        return parse_manifest_toml(manifest_path.read_bytes())
    except Exception:
        return None


def _in_tree_descriptors(roots: list[str]) -> list[PluginDescriptor]:
    """Find in-tree plugins under ``src/phdb/plugins/<name>/plugin.toml``."""
    out: list[PluginDescriptor] = []
    for root_pkg in roots:
        try:
            root_module = importlib.import_module(root_pkg)
        except ImportError:
            continue
        root_paths = list(getattr(root_module, "__path__", []))
        for root_path in root_paths:
            root_p = Path(root_path)
            if not root_p.is_dir():
                continue
            for child in sorted(root_p.iterdir()):
                if not child.is_dir():
                    continue
                manifest_file = child / "plugin.toml"
                if not manifest_file.is_file():
                    continue
                try:
                    manifest = load_manifest(manifest_file)
                except Exception as e:
                    out.append(PluginDescriptor(
                        name=child.name,
                        distribution=None,
                        entry_point_value=f"{root_pkg}.{child.name}",
                        manifest=PluginManifest(
                            name=child.name, version="0.0.0",
                            description="(manifest parse failed)",
                            kind="source", entry_point="",
                        ),
                        source="in_tree",
                        issues=[f"manifest parse failed: {e}"],
                    ))
                    continue
                out.append(PluginDescriptor(
                    name=manifest.name,
                    distribution=None,
                    entry_point_value=manifest.entry_point,
                    manifest=manifest,
                    source="in_tree",
                ))
    return out


def _validate_descriptors(descriptors: list[PluginDescriptor]) -> list[PluginDescriptor]:
    """Annotate descriptors with validation issues (declared schemas, etc.).

    Returns the same list with ``issues`` populated in-place via a new
    descriptor object (descriptors are frozen). Phase 7 plugin loader
    consults ``issues`` before instantiating.
    """
    # Lazy import to avoid cycle with phdb.schemas
    from phdb.schemas.registry import default_schema_registry  # noqa: PLC0415

    schemas_reg = default_schema_registry()
    validated: list[PluginDescriptor] = []
    for d in descriptors:
        issues = list(d.issues)
        m = d.manifest
        if m.kind == "source" and m.source is not None:
            for emit in m.source.emits:
                if schemas_reg.get_by_type(emit) is None:
                    issues.append(f"emits {emit!r} which is not in phdb.schemas registry")
        # Phase 4 will validate facet plugins' node_table existence.
        validated.append(PluginDescriptor(
            name=d.name,
            distribution=d.distribution,
            entry_point_value=d.entry_point_value,
            manifest=d.manifest,
            source=d.source,
            issues=issues,
        ))
    return validated


def discover_plugins(
    *,
    group: str = ENTRY_POINT_GROUP,
    in_tree_roots: list[str] | None = None,
) -> list[PluginDescriptor]:
    """Return source plugins discoverable via entry points + in-tree scan.

    Combines the two sources; in-tree plugins are deduplicated by name
    against entry-point plugins (entry points win since they came from
    pip-installed packages, more authoritative).
    """
    in_tree_roots = in_tree_roots or _IN_TREE_SOURCE_ROOTS
    ep_descriptors = _entry_point_descriptors(group)
    in_tree_descriptors = _in_tree_descriptors(in_tree_roots)
    by_name: dict[str, PluginDescriptor] = {d.name: d for d in in_tree_descriptors}
    for ep_d in ep_descriptors:
        by_name[ep_d.name] = ep_d
    return _validate_descriptors(list(by_name.values()))


def discover_facets(
    *,
    in_tree_roots: list[str] | None = None,
) -> list[PluginDescriptor]:
    """Discover facet plugins (Phase 4 wires this fully)."""
    return discover_plugins(
        group=FACET_ENTRY_POINT_GROUP,
        in_tree_roots=in_tree_roots or _IN_TREE_FACET_ROOTS,
    )


def load_plugin(descriptor: PluginDescriptor) -> PhdbPlugin:
    """Import the plugin class and instantiate it with its manifest.

    Phase 5+ pilot uses this directly; Phase 3 ships it but no plugin
    yet exists to import. Raises ``ImportError`` / ``AttributeError``
    if the entry point can't be resolved.
    """
    if descriptor.issues:
        raise RuntimeError(
            f"plugin {descriptor.name} has validation issues: {descriptor.issues}"
        )
    module_path, class_name = descriptor.entry_point_value.split(":", 1)
    module = importlib.import_module(module_path)
    plugin_cls = getattr(module, class_name)
    plugin: PhdbPlugin = plugin_cls(descriptor.manifest)
    return plugin


def validate_plugin(descriptor: PluginDescriptor) -> list[str]:
    """Return the validation issues for a descriptor (empty list = clean)."""
    return list(descriptor.issues)


__all__ = [
    "ENTRY_POINT_GROUP",
    "FACET_ENTRY_POINT_GROUP",
    "PluginDescriptor",
    "discover_facets",
    "discover_plugins",
    "load_plugin",
    "validate_plugin",
]
