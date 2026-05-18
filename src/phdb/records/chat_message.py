"""ChatMessage record — IM, SMS, Discord, etc."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.common import Attachment, Recipient
from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class ChatMessage:
    """One chat/IM/SMS message."""

    provenance: Provenance
    sender_address: str
    date_sent: str
    is_multipart: bool = False
    has_attachments: bool = False
    attachment_count: int = 0
    platform_id: str | None = None
    sender_name: str | None = None
    body_text: str | None = None
    thread_key: str | None = None
    recipients: tuple[Recipient, ...] = ()
    attachments: tuple[Attachment, ...] = ()
