"""Google Voice adapter — ingests call/text/voicemail HTMLs from Takeout.

Consumes ChatMessage and CallRecord records from phdb.formats.google_voice_html.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.google_voice_html import parse
from phdb.log import get_logger
from phdb.records import CallRecord, ChatMessage

log = get_logger("phdb.adapters.google_voice")

_MAX_BODY_LEN = 5000


class GoogleVoiceAdapter(Adapter):
    """Ingest Google Voice call/text/voicemail HTMLs."""

    name = "google_voice"
    source_kind = "google-voice"
    file_kind = "html"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse(source_path):
            if isinstance(rec, ChatMessage):
                body = (rec.body_text or "")[:_MAX_BODY_LEN]
                yield AdapterRow(
                    schema_type="Message",
                    rfc822_message_id=f"google-voice:{rec.provenance.raw_hash}",
                    subject=f"Text from {rec.sender_address}",
                    sender_address=rec.sender_address,
                    direction="unknown",
                    date_sent=rec.date_sent or None,
                    body_text=body,
                    body_text_source="google-voice-html",
                    raw_hash=rec.provenance.raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest() if body else None,
                    thread_key=rec.thread_key,
                )
            elif isinstance(rec, CallRecord):
                kind_label = rec.call_type.capitalize() if rec.call_type != "voice" else "Received"
                body = f"[{kind_label} call]"
                yield AdapterRow(
                    schema_type="Action",
                    rfc822_message_id=f"google-voice:{rec.provenance.raw_hash}",
                    subject=f"{kind_label} from {rec.caller_address}",
                    sender_address=rec.caller_address,
                    direction=rec.direction,
                    date_sent=rec.date_start or None,
                    body_text=body,
                    body_text_source="google-voice-html",
                    raw_hash=rec.provenance.raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    thread_key=f"google-voice:{rec.caller_address}",
                )
