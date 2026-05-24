"""Vault named-object entity markdown format parser — yields VaultEntityRecord.

Source: 5 Entities/ subdirectories (People, Organizations, Places,
Software, Supplements). Each file whose frontmatter declares a
recognized ``@type`` becomes one record. Folder notes
(``note_type: Folder`` or missing ``@type``) are skipped.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from phdb.formats._frontmatter import parse_frontmatter as _parse_frontmatter
from phdb.records import Provenance

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)

RECOGNIZED_TYPES = frozenset({
    "Person", "Organization", "Corporation", "Periodical",
    "Store", "Restaurant", "HealthClub", "CafeOrCoffeeShop", "Residence", "Place",
    "SoftwareApplication",
    "DietarySupplement",
})

TYPE_TO_TABLE = {
    "Person": "people",
    "Organization": "organizations",
    "Corporation": "organizations",
    "Periodical": "organizations",
    "Store": "entity_places",
    "Restaurant": "entity_places",
    "HealthClub": "entity_places",
    "CafeOrCoffeeShop": "entity_places",
    "Residence": "entity_places",
    "Place": "entity_places",
    "SoftwareApplication": "software_applications",
    "DietarySupplement": "supplements",
}

SUBDIR_TO_TABLE = {
    "People": "people",
    "Organizations": "organizations",
    "Places": "entity_places",
    "Software": "software_applications",
    "Supplements": "supplements",
}


def _scalar(fm: dict[str, object], key: str) -> str | None:
    v = fm.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False) if v else None
    return str(v)


def _jsonlist(fm: dict[str, object], key: str) -> str | None:
    v = fm.get(key)
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False) if v else None
    if v and str(v).strip():
        return json.dumps([str(v)], ensure_ascii=False)
    return None


@dataclass(frozen=True)
class VaultEntityRecord:
    """Intermediate record from a vault entity .md file."""

    provenance: Provenance
    table_name: str
    schema_type: str
    name: str
    file_path: str
    identifier: str | None = None
    additional_type: str | None = None
    # Person-specific
    email: str | None = None
    telephone: str | None = None
    address: str | None = None
    birth_date: str | None = None
    works_for: str | None = None
    url: str | None = None
    same_as: str | None = None
    # Place-specific
    geo: str | None = None
    # Software-specific / Supplements-specific
    categories: str | None = None
    # Supplements-specific
    description: str | None = None
    status: str | None = None
    # Common
    tags: str | None = None


def _resolve_table(schema_type: str, subdir_name: str) -> str | None:
    table = TYPE_TO_TABLE.get(schema_type)
    if table is not None:
        return table
    return SUBDIR_TO_TABLE.get(subdir_name)


def parse(source_path: Path) -> Iterator[VaultEntityRecord]:
    """Walk *source_path* directory tree, yield VaultEntityRecord per entity file."""
    if not source_path.is_dir():
        return

    for md_path in sorted(source_path.rglob("*.md")):
        text = md_path.read_text(encoding="utf-8", errors="replace")
        m = _FM_RE.match(text)
        if not m:
            continue

        fm = _parse_frontmatter(m.group(1))

        note_type = _scalar(fm, "note_type")
        if note_type == "Folder":
            continue

        schema_type = _scalar(fm, "@type")
        if schema_type is None or schema_type not in RECOGNIZED_TYPES:
            continue

        subdir_name = md_path.parent.name
        table_name = _resolve_table(schema_type, subdir_name)
        if table_name is None:
            continue

        rel = md_path.relative_to(source_path)
        file_size = md_path.stat().st_size
        raw_hash = hashlib.sha256(
            f"vault-entity|{rel}|{file_size}".encode()
        ).hexdigest()

        name = _scalar(fm, "name") or _scalar(fm, "title") or md_path.stem

        yield VaultEntityRecord(
            provenance=Provenance(source_path=str(source_path), raw_hash=raw_hash),
            table_name=table_name,
            schema_type=schema_type,
            name=name,
            file_path=str(rel),
            identifier=_scalar(fm, "identifier"),
            additional_type=_scalar(fm, "additionalType"),
            email=_scalar(fm, "email") if table_name == "people" else None,
            telephone=_scalar(fm, "telephone") if table_name in ("people", "entity_places") else None,
            address=_scalar(fm, "address") if table_name in ("people", "entity_places") else None,
            birth_date=_scalar(fm, "birthDate") if table_name == "people" else None,
            works_for=_scalar(fm, "worksFor") if table_name == "people" else None,
            url=_scalar(fm, "url"),
            same_as=_jsonlist(fm, "sameAs") if table_name == "people" else None,
            geo=_scalar(fm, "geo") if table_name == "entity_places" else None,
            categories=_jsonlist(fm, "categories") if table_name in ("software_applications", "supplements") else None,
            description=_scalar(fm, "description") if table_name == "supplements" else None,
            status=_scalar(fm, "status") if table_name == "supplements" else None,
            tags=_jsonlist(fm, "tags"),
        )
