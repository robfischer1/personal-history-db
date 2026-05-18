"""CalendarEvent — iCal VEVENT entries."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class CalendarEvent:
    """One calendar event."""

    provenance: Provenance
    uid: str
    date_start: str
    calendar_name: str = ""
    is_all_day: bool = False
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    organizer: str | None = None
    date_end: str | None = None
    recurrence_rule: str | None = None
    attendees: tuple[str, ...] = ()
