"""Vault consumed-media markdown format parser — yields ConsumedMediaRecord.

Source: 7 Entities/ subdirectories (Books, Games, Movies, Podcasts,
TV Series, YouTube Channels, Twitch Channels). Each file whose
frontmatter declares a recognized ``@type`` becomes one record.
Folder notes (``note_type: Folder`` or missing ``@type``) are skipped.
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
    "Book", "VideoGame", "Movie", "TVSeries",
    "PodcastSeries", "WebSite",
})

TYPE_TO_TABLE = {
    "Book": "books",
    "VideoGame": "games",
    "Movie": "movies",
    "TVSeries": "tv_series",
    "PodcastSeries": "podcasts",
}

SUBDIR_TO_TABLE = {
    "YouTube Channels": "youtube_channels",
    "Twitch Channels": "twitch_channels",
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
    if v:
        return json.dumps([str(v)], ensure_ascii=False)
    return None


def _merge_keywords(fm: dict[str, object]) -> str | None:
    """Merge keywords + categories + genre tags into a single JSON array.

    Per spec: existing vault categories wikilinks and genre-related tags
    are relocated to keywords during ingestion. The genre column stays
    empty for initial ingestion.
    """
    merged: list[str] = []
    for key in ("keywords", "categories"):
        v = fm.get(key)
        if isinstance(v, list):
            merged.extend(v)
        elif v and str(v).strip():
            merged.append(str(v))

    tags = fm.get("tags")
    if isinstance(tags, list):
        for t in tags:
            ts = str(t).strip()
            if ts.startswith("genre/"):
                merged.append(ts)

    return json.dumps(merged, ensure_ascii=False) if merged else None


def _extract_isbn(identifier: str | None) -> str | None:
    if identifier is None:
        return None
    m = re.search(r"ISBN\d*:\s*(\d[\d-]+)", identifier)
    return m.group(1).replace("-", "") if m else None


@dataclass(frozen=True)
class ConsumedMediaRecord:
    """Intermediate record from a vault consumed-media .md file."""

    provenance: Provenance
    table_name: str
    schema_type: str
    name: str
    file_path: str
    description: str | None = None
    url: str | None = None
    image: str | None = None
    identifier: str | None = None
    alternate_name: str | None = None
    author: str | None = None
    publisher: str | None = None
    date_published: str | None = None
    genre: str | None = None
    keywords: str | None = None
    # Book-specific
    isbn: str | None = None
    number_of_pages: int | None = None
    # VideoGame-specific
    game_platform: str | None = None
    # Movie-specific
    duration: str | None = None
    actor: str | None = None
    director: str | None = None
    # TVSeries-specific
    start_date: str | None = None
    number_of_seasons: int | None = None


def _resolve_table(schema_type: str, subdir_name: str) -> str | None:
    """Resolve the target table from @type + parent directory name.

    WebSite is ambiguous — YouTube Channels and Twitch Channels both use
    it. The parent directory disambiguates.
    """
    if schema_type == "WebSite":
        return SUBDIR_TO_TABLE.get(subdir_name)
    return TYPE_TO_TABLE.get(schema_type)


def parse(source_path: Path) -> Iterator[ConsumedMediaRecord]:
    """Walk *source_path* directory tree, yield ConsumedMediaRecord per entity file."""
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
            f"consumed-media|{rel}|{file_size}".encode()
        ).hexdigest()

        name = _scalar(fm, "name") or _scalar(fm, "title") or md_path.stem
        identifier = _scalar(fm, "identifier")

        yield ConsumedMediaRecord(
            provenance=Provenance(source_path=str(source_path), raw_hash=raw_hash),
            table_name=table_name,
            schema_type=schema_type,
            name=name,
            file_path=str(rel),
            description=_scalar(fm, "description"),
            url=_scalar(fm, "url"),
            image=_scalar(fm, "image"),
            identifier=identifier,
            alternate_name=_jsonlist(fm, "aliases"),
            author=_scalar(fm, "creator"),
            publisher=_scalar(fm, "publisher"),
            date_published=_scalar(fm, "datePublished"),
            genre=None,
            keywords=_merge_keywords(fm),
            isbn=_extract_isbn(identifier) if table_name == "books" else None,
            number_of_pages=None,
            game_platform=_jsonlist(fm, "gamePlatform") if table_name == "games" else None,
            duration=_scalar(fm, "duration") if table_name in ("movies",) else None,
            actor=_jsonlist(fm, "actor") if table_name in ("movies", "tv_series") else None,
            director=_scalar(fm, "director") if table_name == "movies" else None,
            start_date=_scalar(fm, "startDate") if table_name in ("tv_series", "podcasts") else None,
            number_of_seasons=None,
        )
