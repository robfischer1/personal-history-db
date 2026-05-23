"""Regression test: project tier must contain zero PII outside allowed contexts.

Scans src/, docs/, tests/, and top-level *.md / *.toml for any of the banned
patterns. This file is excluded from the scan because it necessarily contains
the patterns it forbids (as regex literals).

The single allowed-in-context pattern is `robfischer1` — the maintainer's
GitHub username, which is inherently public via the repo URL and is therefore
permitted only inside `github.com/robfischer1/` URLs.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Patterns banned everywhere in the project tier
HARD_PATTERNS = [
    re.compile(r"robfischer1@gmail\.com", re.IGNORECASE),
    re.compile(r"robfischer@gmail\.com", re.IGNORECASE),
    re.compile(r"webhermit@(gmail|optonline)\.(com|net)", re.IGNORECASE),
    re.compile(r"paradox2@optonline\.net", re.IGNORECASE),
    re.compile(r"\bRob Fischer\b"),
    re.compile(r"\bRobert Fischer\b"),
    re.compile(r"\bgillybeany\b", re.IGNORECASE),
    re.compile(r"C:\\Users\\robfi", re.IGNORECASE),
    re.compile(r"c--Users-robfi", re.IGNORECASE),
    re.compile(r"D:\\04 Archives", re.IGNORECASE),
    re.compile(r"D:\\Records", re.IGNORECASE),
    re.compile(r"D:\\Outputs", re.IGNORECASE),
    re.compile(r"D:\\Photos", re.IGNORECASE),
    re.compile(r"D:\\Reference", re.IGNORECASE),
    re.compile(r"D:\\Software", re.IGNORECASE),
    re.compile(r"D:\\Games", re.IGNORECASE),
    re.compile(r"\bHephaestus\b", re.IGNORECASE),
    re.compile(r"\bRob Inc\b"),
    re.compile(r"\bTao\b"),
    re.compile(r"\bHammer\b"),
    re.compile(r"\bDiuniverse\b"),
    re.compile(r"00008120-000668491105A01E", re.IGNORECASE),
    re.compile(r"f2b31b65-33e0-433e-af65-18058840b49e", re.IGNORECASE),
]

# Allowed only inside github.com URLs (the maintainer's repo URL)
GH_HANDLE = re.compile(r"\brobfischer1\b", re.IGNORECASE)
GH_URL_CONTEXT = "github.com/robfischer1"

SCAN_DIRS = [ROOT / "src", ROOT / "docs", ROOT / "tests"]
SCAN_EXTENSIONS = {".py", ".sql", ".md", ".toml", ".json", ".xml", ".html", ".csv", ".vcf", ".ics"}

# This file necessarily contains the banned patterns as regex literals
EXCLUDED = {Path(__file__).resolve()}


def _iter_files():
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if (
                f.is_file()
                and f.suffix in SCAN_EXTENSIONS
                and f.resolve() not in EXCLUDED
            ):
                yield f
    for f in ROOT.glob("*.md"):
        if f.resolve() not in EXCLUDED:
            yield f


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def test_project_tier_pii_clean() -> None:
    violations: list[str] = []
    for f in _iter_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        rel = f.relative_to(ROOT)
        for pat in HARD_PATTERNS:
            for m in pat.finditer(text):
                violations.append(
                    f"{rel}:{_line_of(text, m.start())}: hard-banned '{m.group()}' "
                    f"(pattern: {pat.pattern})"
                )
        for m in GH_HANDLE.finditer(text):
            ctx = text[max(0, m.start() - 30) : m.end() + 20]
            if GH_URL_CONTEXT not in ctx.lower():
                violations.append(
                    f"{rel}:{_line_of(text, m.start())}: handle outside github URL: "
                    f"'{m.group()}'"
                )

    assert violations == [], "PII found in project tier:\n" + "\n".join(violations)
