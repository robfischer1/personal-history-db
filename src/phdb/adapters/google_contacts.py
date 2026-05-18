"""Google Contacts adapter — ingests Google Takeout vCard exports.

Consumes Contact records from phdb.formats.vcard.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.vcard import _parse_vcf, parse  # noqa: F401
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_contacts")

_MAX_BODY_LEN = 5000


class GoogleContactsAdapter(Adapter):
    """Ingest Google Contacts vCard exports."""

    name = "google_contacts"
    source_kind = "google-contacts"
    file_kind = "vcf"
    schema_type = "Person"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec, group in parse(source_path):
            body_parts = [rec.full_name]
            if rec.organization:
                body_parts.append(f"Org: {rec.organization}")
            if rec.title:
                body_parts.append(f"Title: {rec.title}")
            if rec.emails:
                body_parts.append(f"Emails: {', '.join(rec.emails)}")
            if rec.phones:
                body_parts.append(f"Phones: {', '.join(rec.phones)}")
            body = "\n".join(body_parts)[:_MAX_BODY_LEN]

            primary_addr = rec.emails[0] if rec.emails else (rec.phones[0] if rec.phones else rec.full_name.lower())

            yield AdapterRow(
                schema_type="Person",
                rfc822_message_id=f"google-contacts:{rec.provenance.raw_hash}",
                subject=rec.full_name,
                sender_address=primary_addr,
                sender_name=rec.full_name,
                direction="self",
                body_text=body,
                body_text_source="google-contacts-vcf",
                is_bulk=1,
                bulk_signal="contact-card",
                raw_hash=rec.provenance.raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                thread_key=f"google-contacts:{group}",
            )
