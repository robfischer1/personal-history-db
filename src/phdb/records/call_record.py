"""CallRecord — phone calls and voicemails."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class CallRecord:
    """One phone call event."""

    provenance: Provenance
    caller_address: str
    direction: str
    date_start: str
    call_type: str
    callee_address: str | None = None
    duration_seconds: int | None = None
    voicemail_text: str | None = None
