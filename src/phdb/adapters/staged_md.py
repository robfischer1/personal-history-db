"""Staged markdown adapter — ingests frontmatter+body .md files.

Source: a directory of .md files with YAML frontmatter.
Each file becomes one row. @type from frontmatter drives schema_type.
Per-directory threads.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.staged_md")

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


class StagedMdAdapter(Adapter):
    """Ingest staged personal-history .md files."""

    name = "staged_md"
    source_kind = "staged-md"
    file_kind = "md"
    schema_type = "CreativeWork"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 200

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
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
            date_sent = _coerce_date(fm)
            sub_path = str(md_path).replace("/", "\\")

            raw_seed = f"staged-md|{sub_path}|{md_path.stat().st_size}"
            raw_hash = hashlib.sha256(raw_seed.encode()).hexdigest()
            synthetic_msgid = f"staged-md:{hashlib.sha256(sub_path.encode()).hexdigest()}"

            yield AdapterRow(
                schema_type=schema_type,
                rfc822_message_id=synthetic_msgid,
                subject=name,
                sender_address=self.owner_sender("self")[0],
                sender_name=self.owner_sender("self")[1],
                direction="self",
                date_sent=date_sent,
                body_text=body,
                body_text_source="staged-md",
                raw_hash=raw_hash,
                body_text_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                thread_key=f"staged-md:{cluster_name}",
            )
