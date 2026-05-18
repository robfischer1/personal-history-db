"""Staged markdown format parser — yields DigitalDocument records.

Parses a directory of .md files with YAML frontmatter. Each file becomes
one DigitalDocument record. Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

from phdb.records import DigitalDocument, Provenance

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_VALID_TYPES = {
    "CreativeWork", "Message", "Article", "DigitalDocument",
    "SocialMediaPosting", "EmailMessage", "Book", "Observation",
}


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_block, body = m.group(1), m.group(2)

    out: dict[str, str] = {}
    for raw in fm_block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m2 = re.match(r'^("?)([A-Za-z@_][A-Za-z0-9@_-]*)\1\s*:\s*(.*)$', line)
        if not m2:
            continue
        key, val = m2.group(2), m2.group(3).strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key] = val
    return out, body


def _extract_body_text(body_md: str) -> str:
    s = body_md.strip()
    fence_open = re.search(r"^```[^\n]*\n", s, re.MULTILINE)
    if fence_open:
        rest = s[fence_open.end():]
        fence_close = rest.rfind("\n```")
        if fence_close >= 0:
            return rest[:fence_close].strip()
        return rest.strip()
    s = re.sub(r"^##\s+[^\n]*\n+(---\s*\n+)?", "", s)
    return s.strip()


def _coerce_date(fm: dict[str, str]) -> str | None:
    tc = fm.get("temporalCoverage", "").strip()
    if not tc or tc.startswith("?"):
        return None
    m = re.match(r"(\d{4})", tc)
    if m:
        return f"{m.group(1)}-01-01T00:00:00Z"
    return None


def parse(source_path: Path) -> Iterator[DigitalDocument]:
    """Walk *source_path* directory, parse each .md file, yield DigitalDocument records."""
    if not source_path.is_dir():
        return

    cluster_name = source_path.name
    for md_path in sorted(source_path.rglob("*.md")):
        if md_path.name.lower().startswith("staging readme"):
            continue

        text = md_path.read_text(encoding="utf-8", errors="replace")
        fm, body_md = _parse_frontmatter(text)
        if not fm:
            continue

        body = _extract_body_text(body_md)
        if not body.strip():
            continue

        schema_type = fm.get("@type", "CreativeWork").strip()
        if schema_type not in _VALID_TYPES:
            schema_type = "CreativeWork"

        name = fm.get("name", md_path.stem).strip()
        date_created = _coerce_date(fm)
        sub_path = str(md_path).replace("/", "\\")
        file_size = md_path.stat().st_size

        raw_seed = f"staged-md|{sub_path}|{file_size}"
        raw_hash = hashlib.sha256(raw_seed.encode()).hexdigest()

        yield DigitalDocument(
            provenance=Provenance(source_path=str(md_path), raw_hash=raw_hash),
            title=name,
            body_text=body,
            body_text_source="staged-md",
            file_path=str(md_path.relative_to(source_path)),
            file_size=file_size,
            created_date=date_created,
            bucket=cluster_name,
            document_type=schema_type,
        )
