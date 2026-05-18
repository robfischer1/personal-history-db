"""Unified Facebook adapter — ingests messenger, posts, and residuals from export zips.

Consumes ChatMessage, SocialPost, and Reaction records from
phdb.formats.facebook_html and maps them to AdapterRows.

Replaces: facebook.py, facebook_posts.py, facebook_residuals.py.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.facebook_html import FacebookRecord, parse  # noqa: F401
from phdb.log import get_logger
from phdb.records import ChatMessage, Reaction, SocialPost

log = get_logger("phdb.adapters.facebook_unified")

_POST_TYPE_TO_SCHEMA: dict[str, str] = {
    "status": "SocialMediaPosting",
    "comment": "Comment",
    "group-comment": "Comment",
    "group-post": "SocialMediaPosting",
    "join": "JoinAction",
    "invite": "InviteAction",
    "marketplace": "Conversation",
}

_BULK_POST_TYPES = {"join", "invite", "marketplace", "reaction"}


class FacebookUnifiedAdapter(Adapter):
    """Ingest all Facebook content (messenger, posts, residuals) from export zips."""

    name = "facebook_unified"
    source_kind = "facebook"
    file_kind = "zip"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def _map_chat_message(self, rec: ChatMessage) -> AdapterRow:
        return AdapterRow(
            schema_type="Message",
            rfc822_message_id=f"facebook:{rec.provenance.raw_hash}",
            sender_address=rec.sender_address,
            sender_name=rec.sender_name,
            direction="unknown",
            date_sent=rec.date_sent or None,
            body_text=rec.body_text,
            body_text_source="facebook-html",
            has_attachments=int(rec.has_attachments),
            attachment_count=rec.attachment_count,
            source_byte_offset=rec.provenance.source_byte_offset,
            source_byte_length=rec.provenance.source_byte_length,
            raw_hash=rec.provenance.raw_hash,
            body_text_hash=hashlib.sha256((rec.body_text or "").encode()).hexdigest(),
            thread_key=rec.thread_key,
        )

    def _map_social_post(self, rec: SocialPost) -> AdapterRow:
        schema_type = _POST_TYPE_TO_SCHEMA.get(rec.post_type, "SocialMediaPosting")
        body = rec.body_text or "[empty post]"
        is_bulk = 1 if rec.post_type in _BULK_POST_TYPES else 0

        sender_addr, sender_name = self.owner_sender("facebook")

        return AdapterRow(
            schema_type=schema_type,
            rfc822_message_id=rec.platform_id or f"facebook:{rec.provenance.raw_hash}",
            subject=None,
            sender_address=sender_addr,
            sender_name=sender_name,
            sender_domain="facebook",
            direction="outbound",
            date_sent=rec.date_posted or None,
            body_text=body,
            body_text_source="facebook-html",
            has_attachments=int(rec.has_attachments),
            attachment_count=rec.attachment_count,
            is_bulk=is_bulk,
            source_byte_offset=rec.provenance.source_byte_offset,
            source_byte_length=rec.provenance.source_byte_length,
            raw_hash=rec.provenance.raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
            thread_key=rec.thread_key,
        )

    def _map_reaction(self, rec: Reaction) -> AdapterRow:
        body = rec.target_summary or rec.reaction_type
        sender_addr, sender_name = self.owner_sender("facebook")

        return AdapterRow(
            schema_type="LikeAction",
            rfc822_message_id=f"facebook:reaction:{rec.provenance.raw_hash[:16]}",
            subject=(rec.reaction_type or "like")[:200],
            sender_address=sender_addr,
            sender_name=sender_name,
            sender_domain="facebook",
            direction="outbound",
            date_sent=rec.date_reacted or None,
            body_text=body,
            body_text_source="facebook-html",
            is_bulk=1,
            raw_hash=rec.provenance.raw_hash,
            body_text_hash=hashlib.sha256((body or "").encode()).hexdigest(),
            thread_key="facebook:reaction",
        )

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for record in parse(source_path):
            if isinstance(record, ChatMessage):
                yield self._map_chat_message(record)
            elif isinstance(record, SocialPost):
                yield self._map_social_post(record)
            elif isinstance(record, Reaction):
                yield self._map_reaction(record)
