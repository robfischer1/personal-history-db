"""EmailMessage record — RFC 5322 email."""

from __future__ import annotations

from dataclasses import dataclass, field

from phdb.records.common import Attachment, Recipient
from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class EmailMessage:
    """One email message parsed from mbox or similar format."""

    provenance: Provenance
    rfc822_message_id: str
    sender_address: str
    date_sent: str
    is_multipart: bool = False
    has_attachments: bool = False
    attachment_count: int = 0
    in_reply_to: str | None = None
    references_chain: str | None = None
    subject: str | None = None
    sender_name: str | None = None
    sender_domain: str | None = None
    date_received: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    body_text_source: str | None = None
    is_bulk: bool = False
    bulk_signal: str | None = None
    gmail_thread_id: str | None = None
    gmail_labels: tuple[str, ...] = ()
    recipients: tuple[Recipient, ...] = ()
    attachments: tuple[Attachment, ...] = ()
