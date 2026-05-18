"""Contact — address book entries (vCard, Google Contacts)."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class Contact:
    """One contact/person entry."""

    provenance: Provenance
    full_name: str
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    organization: str | None = None
    title: str | None = None
    birthday: str | None = None
    notes: str | None = None
    addresses: tuple[str, ...] = ()
