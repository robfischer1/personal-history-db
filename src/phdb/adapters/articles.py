"""Articles adapter — ingests vault Resources/Articles/ .md files into the articles table.

Source: the Resources/Articles/ vault directory. Each .md file with
note_type: source-material becomes one `articles` row; the folder note
(note_type: Folder) is skipped. Frontmatter is parsed into typed columns;
the body is stored verbatim for faithful round-trip materialization.

Built for the Articles Dissolution Pilot (Outputs/Plans/Articles Dissolution Pilot.md).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.articles")

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_KEY_RE = re.compile(r'^("?)([A-Za-z@_][A-Za-z0-9@_-]*)\1\s*:(.*)$')
_LIST_ITEM_RE = re.compile(r"^\s+-\s+(.*)$")


def _unquote(s: str) -> str:
    """Strip a single matched pair of surrounding quotes."""
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def _parse_frontmatter(block: str) -> dict[str, object]:
    """Parse a YAML frontmatter block into scalars + simple lists (no yaml dependency).

    Handles `key: scalar`, inline `key: [a, b]`, and block lists
    (`key:` followed by indented `- item` lines).
    """
    out: dict[str, object] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        m = _KEY_RE.match(lines[i])
        if not m:
            i += 1
            continue
        key, rest = m.group(2), m.group(3).strip()
        if rest == "":
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                im = _LIST_ITEM_RE.match(lines[j])
                if not im:
                    break
                items.append(_unquote(im.group(1).strip()))
                j += 1
            if items:
                out[key] = items
                i = j
                continue
            out[key] = ""
            i += 1
        elif rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            out[key] = [_unquote(x.strip()) for x in inner.split(",") if x.strip()]
            i += 1
        else:
            out[key] = _unquote(rest)
            i += 1
    return out


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


class ArticlesAdapter(Adapter):
    """Ingest vault Resources/Articles/ files into the `articles` typed table."""

    name = "articles"
    source_kind = "vault-articles"
    file_kind = "md"
    schema_type = "Article"
    target_table = "articles"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 100

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        if not source_path.is_dir():
            log.warning("articles adapter expects a directory, got %s", source_path)
            return

        for md_path in sorted(source_path.rglob("*.md")):
            text = md_path.read_text(encoding="utf-8", errors="replace")
            m = _FM_RE.match(text)
            if not m:
                log.info("skipping %s — no frontmatter", md_path.name)
                continue

            fm = _parse_frontmatter(m.group(1))
            body = m.group(2)

            if fm.get("note_type") != "source-material":
                log.info(
                    "skipping non-article %s (note_type=%r)",
                    md_path.name, fm.get("note_type"),
                )
                continue

            rel = md_path.relative_to(source_path)
            file_size = md_path.stat().st_size
            raw_hash = hashlib.sha256(
                f"articles|{rel}|{file_size}".encode()
            ).hexdigest()

            extra: dict[str, object] = {
                "url": _scalar(fm, "url"),
                "publisher": _scalar(fm, "publisher"),
                "creator": _scalar(fm, "creator"),
                "description": _scalar(fm, "description"),
                "image_url": _scalar(fm, "image"),
                "categories": _jsonlist(fm, "categories"),
                "tags": _jsonlist(fm, "tags"),
                "aliases": _jsonlist(fm, "aliases"),
                "note_type": _scalar(fm, "note_type"),
                "author_type": _scalar(fm, "author_type"),
                "mtime": _scalar(fm, "updated"),
            }

            yield AdapterRow(
                schema_type="Article",
                subject=_scalar(fm, "name") or md_path.stem,
                body_text=body,
                body_text_source="article-md-verbatim",
                raw_hash=raw_hash,
                file_path=str(rel),
                file_size=file_size,
                ctime=_scalar(fm, "created"),
                bucket="Articles",
                extra=extra,
            )
