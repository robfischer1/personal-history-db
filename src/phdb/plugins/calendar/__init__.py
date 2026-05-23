"""phdb.plugins.calendar — iCal (.ics) calendar ingester.

Phase 7 brief 029 port of the phdb Plugin Architecture plan. Emits to
the ``events`` typed table from one ``.ics`` file (or a directory /
zip of them) following the ``raindrop`` / ``amazon`` single-source +
typed-table-routing precedent.

Replaces the legacy ``phdb.adapters.calendar`` module deleted in the
same commit per Phase 0 Q14 (no shim). Reuses the ``events`` typed
table (migration 0021); no schema changes.
"""

from __future__ import annotations

from phdb.plugins.calendar.plugin import CalendarPlugin

__all__ = ["CalendarPlugin"]
