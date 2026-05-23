"""phdb.facets — first-party facet plugins.

Phase 4 deliverable of the phdb Plugin Architecture plan (2026-05-22).
Five facet plugins ship in-tree: ``people``, ``places``, ``time``,
``threads``, ``topics``. Each subscribes to one facet type emitted by
source plugins via the EmissionBus.

Per Phase 0 Q31 + Q9, third parties can author additional facet
plugins as ``phdb-facet-<name>`` distributions; they discover via the
``phdb.facets`` entry-point group alongside these in-tree ones.

Per WebPage Entity Factoring precedent (2026-05-22): facet plugins
sit ON TOP of entity tables; ``phdb.facets.people`` adds coalescence
rules over the canonical ``persons`` entity table; it does not
recreate Person identity. Same for ``places`` over ``places``.
"""

from __future__ import annotations
