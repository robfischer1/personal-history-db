"""Shared IngestSummary dataclass for plugin run() results.

Consolidates the per-plugin IngestSummary definitions into a single
canonical location. Every plugin can import this instead of defining
its own identical copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)
