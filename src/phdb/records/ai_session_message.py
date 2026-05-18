"""AISessionMessage record — Claude, Gemini, and other AI chat turns."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class AISessionMessage:
    """One turn in an AI conversation session."""

    provenance: Provenance
    date_sent: str
    kind: str
    role: str
    thread_key: str
    body_text: str | None = None
    model: str | None = None
    parent_uuid: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    payload: str | None = None
    thread_metadata: dict[str, object] | None = None
