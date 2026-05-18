"""SocialPost — status updates, tweets, Facebook posts."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class SocialPost:
    """One social media post."""

    provenance: Provenance
    author_name: str
    date_posted: str
    post_type: str
    has_attachments: bool = False
    attachment_count: int = 0
    platform_id: str | None = None
    body_text: str | None = None
    thread_key: str | None = None
    in_reply_to: str | None = None
