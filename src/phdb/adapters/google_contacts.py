"""Google Contacts adapter — ingests Google Takeout vCard exports.

Source: a zip or directory containing ``Takeout/Contacts/<group>/<group>.vcf``
files. Each contact becomes a schema_type='Person' row with is_bulk=1.
Contacts are grouped by their directory into separate threads.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_contacts")

_MAX_BODY_LEN = 5000


def _parse_vcf(text: str) -> list[dict[str, object]]:
    text = text.replace("\r\n ", "").replace("\n ", "")
    contacts: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line == "BEGIN:VCARD":
            current = {
                "emails": [], "phones": [], "orgs": [],
                "titles": [], "addresses": [], "notes": [],
            }
            continue
        if line == "END:VCARD":
            if current and (current.get("fn") or current["emails"] or current["phones"]):
                contacts.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        prop, value = line.split(":", 1)
        prop_main = prop.split(";")[0].upper()
        if prop_main == "FN":
            current["fn"] = value
        elif prop_main == "N":
            parts = value.split(";")
            current["n_last"] = parts[0] if parts else None
            current["n_first"] = parts[1] if len(parts) > 1 else None
        elif prop_main == "EMAIL":
            emails = current["emails"]
            if isinstance(emails, list):
                emails.append(value.lower())
        elif prop_main == "TEL":
            phones = current["phones"]
            if isinstance(phones, list):
                phones.append(re.sub(r"[\s\-().]", "", value))
        elif prop_main == "ORG":
            orgs = current["orgs"]
            if isinstance(orgs, list):
                orgs.append(value.replace(";", " ").strip())
        elif prop_main == "TITLE":
            titles = current["titles"]
            if isinstance(titles, list):
                titles.append(value)
        elif prop_main == "NOTE":
            notes = current["notes"]
            if isinstance(notes, list):
                notes.append(value)
    return contacts


def _yield_vcf_files(source_path: Path) -> Iterator[tuple[str, bytes]]:
    if source_path.is_file() and source_path.suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if name.startswith("Takeout/Contacts/") and name.endswith(".vcf"):
                    yield name, zf.read(name)
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("*.vcf")):
            yield str(p.relative_to(source_path)), p.read_bytes()


class GoogleContactsAdapter(Adapter):
    """Ingest Google Contacts vCard exports."""

    name = "google_contacts"
    source_kind = "google-contacts"
    file_kind = "vcf"
    schema_type = "Person"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for fi, (relpath, vcf_bytes) in enumerate(_yield_vcf_files(source_path)):
            parts = relpath.replace("\\", "/").split("/")
            group = parts[-2] if len(parts) >= 2 else "Default"

            try:
                text = vcf_bytes.decode("utf-8", errors="replace")
            except Exception:
                continue

            contacts = _parse_vcf(text)
            for ci, c in enumerate(contacts):
                fn = str(
                    c.get("fn")
                    or f"{c.get('n_first', '')} {c.get('n_last', '')}".strip()
                    or "Unnamed"
                )

                body_parts = [fn]
                orgs = c.get("orgs", [])
                titles = c.get("titles", [])
                emails = c.get("emails", [])
                phones = c.get("phones", [])

                if isinstance(orgs, list) and orgs:
                    body_parts.append(f"Org: {', '.join(str(o) for o in orgs)}")
                if isinstance(titles, list) and titles:
                    body_parts.append(f"Title: {', '.join(str(t) for t in titles)}")
                if isinstance(emails, list) and emails:
                    body_parts.append(f"Emails: {', '.join(str(e) for e in emails)}")
                if isinstance(phones, list) and phones:
                    body_parts.append(f"Phones: {', '.join(str(p) for p in phones)}")

                body = "\n".join(body_parts)[:_MAX_BODY_LEN]

                primary_addr = ""
                if isinstance(emails, list) and emails:
                    primary_addr = str(emails[0])
                elif isinstance(phones, list) and phones:
                    primary_addr = str(phones[0])
                else:
                    primary_addr = fn.lower()

                dedup_seed = f"google-contacts|{group}|{ci}|{fn}|{primary_addr}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type="Person",
                    rfc822_message_id=f"google-contacts:{raw_hash}",
                    subject=fn,
                    sender_address=primary_addr,
                    sender_name=fn,
                    direction="self",
                    body_text=body,
                    body_text_source="google-contacts-vcf",
                    is_bulk=1,
                    bulk_signal="contact-card",
                    source_byte_offset=fi,
                    source_byte_length=ci,
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    thread_key=f"google-contacts:{group}",
                )
