"""Transaction — purchase orders."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class Transaction:
    """One purchase/order transaction."""

    provenance: Provenance
    order_id: str
    date_ordered: str
    platform: str
    total_amount: float | None = None
    currency: str | None = None
    items: tuple[dict[str, object], ...] = ()
    status: str | None = None
