"""Connection — social graph edges (friend/follow)."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class Connection:
    """One social connection event."""

    provenance: Provenance
    display_name: str
    platform: str
    connection_status: str
    friends_since: str | None = None
    removed_date: str | None = None
    inactive_reason: str | None = None
