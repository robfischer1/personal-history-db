"""phdb.facets.people — Person facet plugin.

Phase 4 deliverable. Subscribes to Person emissions from source
plugins via the EmissionBus; in Phase 4 buffers them via
SkeletonFacetPlugin. Phase 8 fills in the rules-engine coalescer
(TOML rules + manual overrides + audit log) and the MCP tools
``person_timeline`` / ``person_describe``.
"""

from __future__ import annotations

from phdb.facets.people.plugin import PeopleFacetPlugin

__all__ = ["PeopleFacetPlugin"]
