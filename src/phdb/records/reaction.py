"""Reaction — likes, loves, etc. on social posts."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class Reaction:
    """One reaction on a social post (sub-record with parent_id)."""

    provenance: Provenance
    parent_id: str
    reactor_name: str
    reaction_type: str
    date_reacted: str
    target_summary: str | None = None
