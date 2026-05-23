# Port `calendar` adapter to plugin

Port `phdb.adapters.calendar` → `phdb.plugins.calendar/`. Source: iCal
(.ics) calendar exports — events + invites + reminders.

## Manifest declarations

- `emits = ["Event", "InviteAction", "Action"]`
- `entity_refs = []`
- `formats_used = ["ical"]`
- `records_required = ["CalendarEvent"]`
- `facets_projected = ["Time", "Person"]`

## Initial scope

- Port the ICS parser + per-component routing (VEVENT → Event;
  VTODO with attendee → InviteAction; calendar reminders → Action).
- Project each attendee email to the Person facet.
- Project start/end timestamps to Time.

## Out of scope

- Recurring-event expansion beyond the existing logic.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_calendar_adapter.py` passes verbatim.

## Context

calendar is the canonical Time-facet source. Recurring events plus
sparse attendee lists make Person facet emission heterogeneous; good
test for the bus's tolerance of optional payloads.
