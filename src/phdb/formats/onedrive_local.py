"""OneDrive local directory format parser — yields DigitalDocument records.

Walks Outputs/, Reference/, Records/ top-level pillars under a local OneDrive
root directory. Extracts text from documents using shared extractors.

Reference/ has a body-extract allowlist: active-pursuit subdirs get full
body extraction; everything else yields metadata-only records (body_text=None).

Pure parser: no DB knowledge, no identity, no adapter framework dependency.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.formats.document_extract import EXTRACTORS, MAX_BODY_LEN
from phdb.records import DigitalDocument, Provenance

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024

INCLUDE_TOP_DIRS = {"Outputs", "Reference", "Records"}

REFERENCE_BODY_ALLOWLIST = {
    "Mind Tools", "Weight Training Programs", "Weight Training Theory",
    "Nutrition", "Cooking", "Personal Development", "Spirituality",
    "Philosophy", "Mental Health", "Entrepreneurship", "ADHD",
    "Fat Loss", "Marek Health Physical", "Medicaid", "MMA", "Rugby",
    "Career Choice", "Sexuality", "Kink", "Supplements",
    "People",
}

TEXT_EXTENSIONS = {
    ".docx", ".pdf", ".txt", ".md", ".html", ".htm",
    ".ipynb", ".xlsx", ".xls", ".csv", ".json", ".rtf",
}

SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".gif", ".bmp", ".webp", ".svg", ".ico",
    ".mp3", ".wav", ".mp4", ".mov", ".m4a", ".aac", ".flac",
    ".epub", ".mobi", ".azw", ".azw3", ".opf",
    ".py", ".java", ".lua", ".php", ".js", ".ts",
    ".exe", ".dll", ".apk", ".dmg", ".zip", ".gz", ".tar",
    ".dds", ".tga", ".blp",
    ".db", ".sqlite", ".sqlite3",
}


def _derive_bucket(rel_parts: tuple[str, ...]) -> str:
    """Derive a bucket label from the relative path parts."""
    if not rel_parts:
        return "(root)"
    if len(rel_parts) == 1:
        return rel_parts[0]
    return f"{rel_parts[0]}/{rel_parts[1]}"


def _is_reference_body_allowed(rel_parts: tuple[str, ...]) -> bool:
    """Check if a file under Reference/ is in the body-extract allowlist."""
    if len(rel_parts) < 2 or rel_parts[0] != "Reference":
        return True
    return rel_parts[1] in REFERENCE_BODY_ALLOWLIST


def parse(source_path: Path) -> Iterator[DigitalDocument]:
    """Walk source_path's Outputs/Reference/Records/ dirs, yield DigitalDocument records."""
    candidate_dirs = [
        source_path / d
        for d in sorted(INCLUDE_TOP_DIRS)
        if (source_path / d).is_dir()
    ]

    for top_dir in candidate_dirs:
        for fpath in sorted(top_dir.rglob("*")):
            if not fpath.is_file():
                continue

            suffix = fpath.suffix.lower()
            if suffix in SKIP_EXTENSIONS:
                continue
            if suffix not in TEXT_EXTENSIONS:
                continue

            try:
                stat = fpath.stat()
                if stat.st_size > MAX_FILE_SIZE_BYTES:
                    continue
            except OSError:
                continue

            rel_parts = fpath.relative_to(source_path).parts
            bucket = _derive_bucket(rel_parts)
            relpath = str(fpath.relative_to(source_path))
            path_hash = hashlib.sha1(str(fpath).encode()).hexdigest()[:16]

            try:
                mtime = datetime.fromtimestamp(
                    stat.st_mtime, tz=UTC
                ).isoformat()
            except OSError:
                mtime = None

            try:
                ctime = datetime.fromtimestamp(
                    stat.st_ctime, tz=UTC
                ).isoformat()
            except OSError:
                ctime = None

            raw_hash = hashlib.sha256(
                f"onedrive|onedrive:{path_hash}".encode()
            ).hexdigest()

            body_text: str | None = None
            body_text_source: str | None = None

            if _is_reference_body_allowed(rel_parts):
                entry = EXTRACTORS.get(suffix)
                if not entry:
                    continue
                label, extractor = entry
                try:
                    data = fpath.read_bytes()
                except Exception:
                    continue
                body = extractor(data)
                if not body or not body.strip():
                    continue
                body_text = body[:MAX_BODY_LEN]
                body_text_source = label
            # else: metadata-only record (body_text=None)

            yield DigitalDocument(
                provenance=Provenance(
                    source_path=str(fpath),
                    raw_hash=raw_hash,
                ),
                title=fpath.name,
                body_text=body_text,
                body_text_source=body_text_source,
                file_path=relpath,
                file_size=stat.st_size,
                modified_date=mtime,
                created_date=ctime,
                bucket=bucket,
            )
