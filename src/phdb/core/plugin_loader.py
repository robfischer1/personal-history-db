"""Plugin loader — entry-point discovery + manifest validation.

Phase 1 deliverable scaffold per the phdb Plugin Architecture plan. The
loader walks ``importlib.metadata.entry_points()`` for the
``phdb.plugins`` group, loads manifests, and returns descriptors. The
full ``PluginManifest`` dataclass + ABC contract land in Phase 3; this
module ships the discovery seam so other Phase 1+ code can begin to
depend on it.

In-tree plugins (``phdb.plugins.<name>``) sit alongside pip-installed
``phdb-plugin-<name>`` packages and use the same entry-point group; the
two paths are unified at discovery time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

ENTRY_POINT_GROUP = "phdb.plugins"
FACET_ENTRY_POINT_GROUP = "phdb.facets"


@dataclass(frozen=True)
class PluginDescriptor:
    """Loaded plugin entry, returned by ``discover_plugins()``.

    Phase 1 carries only the discovery payload — Phase 3 will swap
    ``manifest`` from ``dict[str, Any]`` to the typed
    ``PluginManifest`` dataclass.
    """

    name: str
    distribution: str | None
    entry_point_value: str
    manifest: dict[str, Any] = field(default_factory=dict)


def discover_plugins(
    *,
    group: str = ENTRY_POINT_GROUP,
) -> list[PluginDescriptor]:
    """Return the list of plugins discoverable via Python entry points.

    Phase 1: returns an empty list until any plugin actually registers an
    entry point. The loader exists so Phase 3+ code has a stable seam to
    target.
    """
    out: list[PluginDescriptor] = []
    try:
        eps = entry_points(group=group)
    except TypeError:
        # Pre-3.10 entry_points API returned a SelectableGroups; the project
        # targets >=3.11 (pyproject) but be defensive in case of stub shims.
        eps = entry_points().get(group, [])  # type: ignore[assignment]
    for ep in eps:
        dist_name: str | None = None
        try:
            dist_name = ep.dist.name if ep.dist else None  # type: ignore[union-attr]
        except AttributeError:
            dist_name = None
        out.append(PluginDescriptor(
            name=ep.name,
            distribution=dist_name,
            entry_point_value=ep.value,
        ))
    return out


def discover_facets() -> list[PluginDescriptor]:
    """Discover facet plugins via the ``phdb.facets`` entry-point group."""
    return discover_plugins(group=FACET_ENTRY_POINT_GROUP)
