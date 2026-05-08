"""Gemini web landmark adapter.

Ingests vault landmark markdown files from Timelines/AI Sessions/Gemini - *.md.
One thread row per landmark file (thread_key = URL slug from frontmatter).
One message row per ad-prompt / ad-ai-response callout block.

File body structure:
  - /share files (Trunk + standalones): optional "[url](url)" link line,
    optional "Created with Pro ... Published ..." metadata line, then blocks.
  - /app files (branches): optional "From [Branch • ]<root_name>" lineage line
    (encoded at the top of the body), then blocks.

Block format:
  ```ad-prompt
  title: (`HH:MM`) Rob prompted
  [blank line]
  [content...]
  ```
  ```ad-ai-response
  title: (`HH:MM`) Gemini responded
  [blank line]
  [content...]
  ```

Timestamps in titles are either 24-hour ("07:35") or 12-hour ("4:46 PM" / "4:23 AM").
Date comes from frontmatter `created:` field. Stored as naive ISO-8601 (no TZ).

Thread metadata stored as JSON:
  {"url": ..., "name": ..., "lineage_string": ..., "depth": 0/1/2/3}

parent_thread_key linkage is NOT resolved at ingest time; it is stored as a
"parent_url_slug" key in thread_metadata for post-processing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy


# ── Frontmatter parsing ───────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from body. Returns (fields, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end]
    body = text[end + 4:].lstrip("\n")
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip().strip('"')
    return fields, body


# ── URL slug ─────────────────────────────────────────────────────────────────

_URL_SLUG_RE = re.compile(r"gemini\.google\.com/(share|app)/([a-z0-9]+)", re.IGNORECASE)


def _url_to_slug(url: str) -> str | None:
    """Convert Gemini URL to a compact slug: gemini-{share|app}-{id}."""
    m = _URL_SLUG_RE.search(url)
    if not m:
        return None
    return f"gemini-{m.group(1).lower()}-{m.group(2).lower()}"


# ── Lineage parsing ───────────────────────────────────────────────────────────

def _parse_lineage(body: str) -> tuple[str | None, int]:
    """Return (lineage_string, depth) from the first non-empty body line."""
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("From "):
            rest = line[5:]
            parts = [p.strip() for p in rest.split(" • ")]
            depth = sum(1 for p in parts if p == "Branch") + 1
            return line, depth
        # Not a lineage prefix; stop scanning
        return None, 0
    return None, 0


# ── Timestamp parsing ─────────────────────────────────────────────────────────

_TITLE_TIME_RE = re.compile(r"\(`([^`]+)`\)")

_TIME_FORMATS = [
    "%I:%M %p",   # 4:46 PM
    "%H:%M",      # 07:35
    "%I:%M%p",    # 4:46PM (no space)
]


def _parse_time(raw: str) -> str | None:
    """Parse a time string from a block title. Returns 'HH:MM' or None."""
    raw = raw.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def _build_date_sent(date_str: str, time_hhmm: str | None) -> str:
    """Combine YYYY-MM-DD date with HH:MM time into ISO-8601 naive string."""
    if not time_hhmm:
        return f"{date_str}T00:00:00"
    return f"{date_str}T{time_hhmm}:00"


# ── Block parsing ─────────────────────────────────────────────────────────────

_BLOCK_OPEN_RE = re.compile(r"^```ad-(prompt|ai-response)$")
_BLOCK_CLOSE = "```"
_TITLE_TS_RE = re.compile(r"title:\s*\(`([^`]+)`\)")


def _iter_blocks(body: str) -> Iterator[tuple[str, str, str]]:
    """Yield (block_type, title_line, content_text) for each ad-block.

    Handles both properly-closed blocks and the final unclosed block (no
    closing fence at EOF — common in Gemini share-page landmark files).
    """
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        m = _BLOCK_OPEN_RE.match(lines[i].rstrip())
        if not m:
            i += 1
            continue
        block_type = m.group(1)
        i += 1
        # Next line should be the title
        title_line = lines[i].strip() if i < len(lines) else ""
        i += 1
        # Skip optional blank line after title
        if i < len(lines) and lines[i].strip() == "":
            i += 1
        # Collect content until closing fence or EOF
        content_lines: list[str] = []
        while i < len(lines):
            if lines[i].rstrip() == _BLOCK_CLOSE:
                i += 1  # consume the closing fence
                break
            # A new ad-block opening also terminates this block
            if _BLOCK_OPEN_RE.match(lines[i].rstrip()):
                break
            content_lines.append(lines[i])
            i += 1
        # Strip trailing blank lines from content
        while content_lines and not content_lines[-1].strip():
            content_lines.pop()
        yield block_type, title_line, "\n".join(content_lines)


class GeminiWebAdapter(Adapter):
    """Ingester for Gemini web landmark markdown files."""

    name = "gemini_web"
    source_kind = "gemini-web"
    file_kind = "md"
    schema_type = "Conversation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        text = source_path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)

        url = fm.get("url", "")
        name = fm.get("name", source_path.stem)
        created = fm.get("created", "")  # YYYY-MM-DD

        thread_key = _url_to_slug(url)
        if not thread_key:
            # Fallback: slugify the filename
            thread_key = re.sub(r"[^a-z0-9]+", "-", source_path.stem.lower()).strip("-")

        lineage_string, depth = _parse_lineage(body)

        thread_metadata = json.dumps({
            "url": url,
            "name": name,
            "lineage_string": lineage_string,
            "depth": depth,
        })

        # Parse ad-prompt / ad-ai-response blocks (handles unclosed final blocks)
        for idx, (block_type, title_line, content) in enumerate(_iter_blocks(body)):
            role = "user" if block_type == "prompt" else "assistant"

            time_hhmm: str | None = None
            ts_m = _TITLE_TS_RE.search(title_line)
            if ts_m:
                time_hhmm = _parse_time(ts_m.group(1))

            date_sent = _build_date_sent(created, time_hhmm) if created else None
            body_text = content or None
            raw_hash = f"gemini-web:{thread_key}:{idx}"

            yield AdapterRow(
                schema_type="Conversation",
                date_sent=date_sent,
                body_text=body_text,
                is_bulk=0,
                raw_hash=raw_hash,
                kind="message",
                role=role,
                parent_uuid=None,
                tool_name=None,
                tool_use_id=None,
                model=None,
                payload=title_line + "\n" + content,
                thread_key=thread_key,
                thread_metadata=thread_metadata,
                thread_cwd=None,
            )
