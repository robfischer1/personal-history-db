"""phdb.plugins.google_timeline — Google Maps Timeline location history ingester.

Phase 7 brief 030 port of the phdb Plugin Architecture plan. Emits to
three typed tables (``places`` / ``travel_actions`` / ``geo_shapes``)
from a single Google Timeline ``locationhistory.json`` export and
writes per-waypoint rows to the ``geo_traces`` sidecar.

Replaces the legacy ``phdb.adapters.google_timeline`` module deleted in
the same commit per Phase 0 Q14 (no shim). Reuses the typed tables
introduced in migration 0021 + the ``geo_traces`` sidecar from
migration 0003; no schema changes.
"""

from __future__ import annotations

from phdb.plugins.google_timeline.plugin import GoogleTimelinePlugin

__all__ = ["GoogleTimelinePlugin"]
