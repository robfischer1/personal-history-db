"""vCard format parser — yields Contact records from .vcf files.

Handles Google Takeout zip or directory of vCard files.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

from phdb.records import Contact, Provenance


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


def parse(source_path: Path) -> Iterator[tuple[Contact, str]]:
    """Parse vCard files, yielding (Contact, group_name) tuples."""
    source_str = str(source_path)

    for _fi, (relpath, vcf_bytes) in enumerate(_yield_vcf_files(source_path)):
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

            emails = c.get("emails", [])
            phones = c.get("phones", [])
            orgs = c.get("orgs", [])
            titles = c.get("titles", [])
            notes_list = c.get("notes", [])

            primary_addr = ""
            if isinstance(emails, list) and emails:
                primary_addr = str(emails[0])
            elif isinstance(phones, list) and phones:
                primary_addr = str(phones[0])
            else:
                primary_addr = fn.lower()

            dedup_seed = f"google-contacts|{group}|{ci}|{fn}|{primary_addr}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            yield Contact(
                provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                full_name=fn,
                emails=tuple(str(e) for e in emails) if isinstance(emails, list) else (),
                phones=tuple(str(p) for p in phones) if isinstance(phones, list) else (),
                organization=(", ".join(str(o) for o in orgs) if isinstance(orgs, list) and orgs else None),
                title=(", ".join(str(t) for t in titles) if isinstance(titles, list) and titles else None),
                notes=("\n".join(str(n) for n in notes_list) if isinstance(notes_list, list) and notes_list else None),
            ), group
