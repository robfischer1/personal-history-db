"""Shared sub-structures used by multiple record types."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class Recipient:
    """Denormalized recipient (not a first-class record)."""

    address: str
    name: str | None = None
    rtype: str = "to"


@dataclass(frozen=True)
class Attachment:
    """First-class child record for email/chat attachments."""

    provenance: Provenance
    parent_id: str
    filename: str | None = None
    content_type: str | None = None
    content_disposition: str | None = None
    size_bytes: int | None = None
    on_disk_path: str | None = None
    content_hash: str | None = None
