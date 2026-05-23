"""EmissionBus — source plugin → facet plugin dispatch.

Phase 4 deliverable. At ingest time, a source plugin's ``project_facets``
hook emits ``FacetEmission`` events; the bus dispatches each event to
every installed facet plugin whose manifest declares
``consumes == emission.facet_type``.

The bus is in-process and synchronous; persistence + replay live in
the per-facet audit log (``facet_coalescence_log`` — wired by the
people/places facet plugins in Phase 8). The bus itself stays
transient — emissions are not stored centrally; each facet decides
what to keep.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from phdb.core.plugin.contract import PhdbFacetPlugin


@dataclass(frozen=True)
class FacetEmission:
    """One projection event from a source plugin to facets.

    ``facet_type`` matches the consuming facet plugin's
    ``manifest.facet.consumes``. ``payload`` is plugin-defined — for a
    Person emission it might be ``{"email": "...", "display_name":
    "...", "source_address": "..."}``; for a Place emission,
    ``{"name": "...", "lat": ..., "lon": ..., "source_id": ...}``.
    """

    source_table: str
    source_id: int
    facet_type: str
    payload: dict[str, Any]


@dataclass
class EmissionBus:
    """In-process dispatch hub from source plugins to facet plugins."""

    subscribers: dict[str, list[PhdbFacetPlugin]] = field(default_factory=lambda: defaultdict(list))

    def subscribe(self, plugin: PhdbFacetPlugin) -> None:
        """Register a facet plugin to receive its declared facet type."""
        facet_type = plugin.manifest.facet.consumes if plugin.manifest.facet else None
        if not facet_type:
            raise ValueError(
                f"plugin {plugin.name} has no facet.consumes — cannot subscribe"
            )
        self.subscribers[facet_type].append(plugin)

    def dispatch(self, emission: FacetEmission) -> int:
        """Deliver one emission to all subscribers of its facet_type.

        Returns the number of subscribers notified.
        """
        delivered = 0
        for plugin in self.subscribers.get(emission.facet_type, []):
            plugin.consume(emission)
            delivered += 1
        return delivered

    def emit(
        self,
        *,
        source_table: str,
        source_id: int,
        facet_type: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Convenience — build a FacetEmission and dispatch it in one call."""
        return self.dispatch(FacetEmission(
            source_table=source_table,
            source_id=source_id,
            facet_type=facet_type,
            payload=payload or {},
        ))


__all__ = [
    "EmissionBus",
    "FacetEmission",
]
