"""Vault article markdown format parser — yields ArticleRecord intermediates.

Source: a directory of Resources/Articles/ .md files with YAML frontmatter.
Each file with note_type: source-material becomes one ArticleRecord.
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


def _scalar(fm: dict[str, object], key: str) -> str | None:
    """Return a frontmatter value as a scalar string, or None if absent/empty.

    A list-valued field (e.g. `publisher` stored as a wikilink list) is
    JSON-encoded so no information is lost.
    """
    v = fm.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False) if v else None
    return str(v)


def _jsonlist(fm: dict[str, object], key: str) -> str | None:
    """Return a list-valued frontmatter field as a JSON array string, or None."""
    v = fm.get(key)
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False) if v else None
    if v:
        return json.dumps([str(v)], ensure_ascii=False)
    return None


@dataclass(frozen=True)
class ArticleRecord:
    """Intermediate record from a vault article .md file."""

    provenance: Provenance
    title: str
    body_text: str
    body_text_source: str
    file_path: str
    file_size: int
    ctime: str | None
    bucket: str
    url: str | None = None
    publisher: str | None = None
    creator: str | None = None
    description: str | None = None
    image_url: str | None = None
    categories: str | None = None
    tags: str | None = None
    aliases: str | None = None
    note_type: str | None = None
    author_type: str | None = None
    mtime: str | None = None


def parse(source_path: Path) -> Iterator[ArticleRecord]:
    """Walk *source_path* directory, parse each article .md file, yield ArticleRecord."""
    if not source_path.is_dir():
        return

    for md_path in sorted(source_path.rglob("*.md")):
        text = md_path.read_text(encoding="utf-8", errors="replace")
        m = _FM_RE.match(text)
        if not m:
            continue

        fm = _parse_frontmatter(m.group(1))
        body = m.group(2)

        if fm.get("note_type") != "source-material":
            continue

        rel = md_path.relative_to(source_path)
        file_size = md_path.stat().st_size
        raw_hash = hashlib.sha256(
            f"articles|{rel}|{file_size}".encode()
        ).hexdigest()

        yield ArticleRecord(
            provenance=Provenance(source_path=str(source_path), raw_hash=raw_hash),
            title=_scalar(fm, "name") or md_path.stem,
            body_text=body,
            body_text_source="article-md-verbatim",
            file_path=str(rel),
            file_size=file_size,
            ctime=_scalar(fm, "created"),
            bucket="Articles",
            url=_scalar(fm, "url"),
            publisher=_scalar(fm, "publisher"),
            creator=_scalar(fm, "creator"),
            description=_scalar(fm, "description"),
            image_url=_scalar(fm, "image"),
            categories=_jsonlist(fm, "categories"),
            tags=_jsonlist(fm, "tags"),
            aliases=_jsonlist(fm, "aliases"),
            note_type=_scalar(fm, "note_type"),
            author_type=_scalar(fm, "author_type"),
            mtime=_scalar(fm, "updated"),
        )
