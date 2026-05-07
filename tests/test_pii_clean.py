"""Regression test: project-tier code must contain zero PII."""

from __future__ import annotations

import re
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src" / "phdb"

PII_PATTERNS = [
    re.compile(r"robfischer1@gmail\.com", re.IGNORECASE),
    re.compile(r"robfischer@gmail\.com", re.IGNORECASE),
    re.compile(r"webhermit@gmail\.com", re.IGNORECASE),
    re.compile(r"webhermit@optonline\.net", re.IGNORECASE),
    re.compile(r"paradox2@optonline\.net", re.IGNORECASE),
    re.compile(r"\brobfischer1\b", re.IGNORECASE),
    re.compile(r"\b<owner-name>\b"),
    re.compile(r"\b<owner-name>\b"),
    re.compile(r"\b<owner-handle>\b", re.IGNORECASE),
    re.compile(r"C:\\Users\\<owner>", re.IGNORECASE),
    re.compile(r"D:\\04 Archives", re.IGNORECASE),
]


def test_project_code_pii_clean() -> None:
    """src/phdb/ must contain zero PII terms."""
    violations: list[str] = []
    for py_file in SRC_DIR.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for pat in PII_PATTERNS:
            for match in pat.finditer(text):
                rel = py_file.relative_to(SRC_DIR)
                violations.append(f"{rel}: matched '{match.group()}' (pattern: {pat.pattern})")

    assert violations == [], "PII found in project code:\n" + "\n".join(violations)
