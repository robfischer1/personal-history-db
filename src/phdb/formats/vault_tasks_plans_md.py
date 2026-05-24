"""Vault task/plan markdown format parser — yields TaskRecord / PlanRecord.

Source: Outputs/Tasks/ + System/Tasks/ (tasks), Outputs/Plans/ + System/Plans/
(plans). Each file whose frontmatter declares ``note_type: Task`` or
``note_type: Plan`` becomes one record. DECISIONS.md sidecars and folder
notes are skipped.

Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from phdb.formats._frontmatter import parse_frontmatter as _parse_frontmatter
from phdb.records import Provenance

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

SKIP_FILENAMES = frozenset({
    "Tasks.md", "Plans.md", "Spec-Kit Brief Queue.md",
})


def _str(val: object) -> str | None:
    if val is None or val == "":
        return None
    return str(val)


@dataclass(frozen=True)
class TaskRecord:
    name: str
    identifier: str | None
    tier: str | None
    status: str
    effort: str | None
    maintenance: str | None
    project: str | None
    created: str | None
    updated: str | None
    closure_date: str | None
    closure_evidence: str | None
    file_path: str
    provenance: Provenance


@dataclass(frozen=True)
class PlanRecord:
    name: str
    identifier: str | None
    description: str | None
    status: str
    phase: str | None
    effort: str | None
    maintenance: str | None
    created: str | None
    updated: str | None
    file_path: str
    provenance: Provenance


def _compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _is_decisions_file(path: Path) -> bool:
    return "DECISIONS" in path.name


def parse_tasks(root: Path) -> Iterator[TaskRecord]:
    """Walk a directory (or single file) yielding TaskRecord objects."""
    paths: list[Path] = []
    if root.is_file():
        paths = [root]
    elif root.is_dir():
        paths = sorted(root.glob("*.md"))

    for fp in paths:
        if fp.name in SKIP_FILENAMES or _is_decisions_file(fp):
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception:
            continue

        m = _FM_RE.match(text)
        if not m:
            continue

        fm = _parse_frontmatter(m.group(1))
        note_type = _str(fm.get("note_type"))
        if not note_type or note_type.lower() != "task":
            continue

        name = _str(fm.get("name")) or fp.stem
        status = _str(fm.get("status")) or "open"
        raw_hash = _compute_hash(text)

        yield TaskRecord(
            name=name,
            identifier=_str(fm.get("identifier")),
            tier=_str(fm.get("tier")),
            status=status,
            effort=_str(fm.get("effort")),
            maintenance=_str(fm.get("maintenance")),
            project=_str(fm.get("project")),
            created=_str(fm.get("created")),
            updated=_str(fm.get("updated")),
            closure_date=_str(fm.get("closure_date")),
            closure_evidence=_str(fm.get("closure_evidence")),
            file_path=str(fp),
            provenance=Provenance(source_path=str(fp), raw_hash=raw_hash),
        )


def parse_plans(root: Path) -> Iterator[PlanRecord]:
    """Walk a directory (or single file) yielding PlanRecord objects."""
    paths: list[Path] = []
    if root.is_file():
        paths = [root]
    elif root.is_dir():
        paths = sorted(root.glob("*.md"))

    for fp in paths:
        if fp.name in SKIP_FILENAMES or _is_decisions_file(fp):
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception:
            continue

        m = _FM_RE.match(text)
        if not m:
            continue

        fm = _parse_frontmatter(m.group(1))
        note_type = _str(fm.get("note_type"))
        if not note_type or note_type.lower() != "plan":
            continue

        name = _str(fm.get("name")) or _str(fm.get("title")) or fp.stem
        status = _str(fm.get("status")) or "draft"
        raw_hash = _compute_hash(text)

        yield PlanRecord(
            name=name,
            identifier=_str(fm.get("identifier")),
            description=_str(fm.get("description")),
            status=status,
            phase=_str(fm.get("phase")),
            effort=_str(fm.get("effort")),
            maintenance=_str(fm.get("maintenance")),
            created=_str(fm.get("created")),
            updated=_str(fm.get("updated")),
            file_path=str(fp),
            provenance=Provenance(source_path=str(fp), raw_hash=raw_hash),
        )
