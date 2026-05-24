"""Shared frontmatter parsing utilities for vault markdown format parsers.

Consolidated from identical copies in articles_md, clippings_md,
consumed_media_md, vault_entity_md, and vault_tasks_plans_md.

NOTE: staged_md.py has a different return type (tuple) and is intentionally
not included here.
"""

from __future__ import annotations

import re

KEY_RE = re.compile(r'^("?)([A-Za-z@_][A-Za-z0-9@_-]*)\1\s*:(.*)$')
LIST_ITEM_RE = re.compile(r"^\s+-\s+(.*)$")


def _unquote(s: str) -> str:
    """Strip a single matched pair of surrounding quotes."""
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def parse_frontmatter(block: str) -> dict[str, object]:
    """Parse a YAML frontmatter block into scalars + simple lists (no yaml dependency).

    Handles ``key: scalar``, inline ``key: [a, b]``, and block lists
    (``key:`` followed by indented ``- item`` lines).
    """
    out: dict[str, object] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        m = KEY_RE.match(lines[i])
        if not m:
            i += 1
            continue
        key, rest = m.group(2), m.group(3).strip()
        if rest == "":
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                im = LIST_ITEM_RE.match(lines[j])
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
