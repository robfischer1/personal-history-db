"""DigitalDocument — files from Drive, OneDrive, notes, staged markdown."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class DigitalDocument:
    """One document/file with extracted text content."""

    provenance: Provenance
    title: str | None = None
    body_text: str | None = None
    body_text_source: str | None = None
    file_path: str | None = None
    file_size: int | None = None
    created_date: str | None = None
    modified_date: str | None = None
    bucket: str | None = None
    mime_type: str | None = None
    document_type: str | None = None
