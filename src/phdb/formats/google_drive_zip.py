"""Google Drive zip/directory format parser — yields DigitalDocument records.

Source: a Google Takeout zip (or extracted directory) containing ``Drive/``
paths with text-bearing documents.

Pure parser: no DB knowledge, no identity. Yields typed DigitalDocument
records that the google_drive adapter consumes.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from phdb.formats.document_extract import EXTRACTORS, MAX_BODY_LEN
from phdb.records import DigitalDocument, Provenance

TEXT_EXTENSIONS = {
    ".docx", ".pdf", ".txt", ".md", ".html", ".htm",
    ".ipynb", ".xlsx", ".xls", ".csv", ".json", ".rtf",
}

SKIP_EXTENSIONS = {
    ".feather", ".gz", ".tar", ".zip", ".bz2", ".xz",
    ".jpg", ".jpeg", ".png", ".heic", ".gif", ".ico", ".svg", ".bmp", ".webp",
    ".mp3", ".wav", ".mov", ".mp4", ".m4a", ".aac",
    ".vcf", ".kmz", ".kml", ".properties", ".apk", ".exe", ".dll", ".dmg",
    ".parquet",
}

SKIP_PATH_PATTERNS = [
    re.compile(r"TitaniumBackup"),
    re.compile(r"\.gdrive$"),
    re.compile(r"/Trash/"),
    re.compile(r"/Copy of [^/]+$"),
]


def derive_bucket(relpath: str) -> str:
    """First 2 path components after Drive/."""
    parts = relpath.split("/")
    while parts and parts[0] in ("Takeout", "Drive"):
        parts = parts[1:]
    if len(parts) <= 1:
        return "(root)"
    if parts[0].startswith(("00 ", "01 ", "02 ", "03 ", "04 ")) and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _collect_docx_stems(names: list[str]) -> set[tuple[str, str]]:
    stems: set[tuple[str, str]] = set()
    for n in names:
        p = Path(n)
        if p.suffix.lower() == ".docx":
            stems.add((str(p.parent), p.stem))
    return stems


def _is_pdf_shadowed_by_docx(name: str, docx_stems: set[tuple[str, str]]) -> bool:
    p = Path(name)
    if p.suffix.lower() != ".pdf":
        return False
    return (str(p.parent), p.stem) in docx_stems


def _passes_skip_filters(name: str) -> bool:
    if any(p.search(name) for p in SKIP_PATH_PATTERNS):
        return False
    ext = Path(name).suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return False
    return ext in TEXT_EXTENSIONS


def parse(source_path: Path) -> Iterator[DigitalDocument]:
    """Parse a Google Takeout zip or extracted directory, yielding DigitalDocument records."""
    if source_path.is_file() and source_path.suffix == ".zip":
        yield from _parse_zip(source_path)
    elif source_path.is_dir():
        yield from _parse_dir(source_path)


def _parse_zip(zip_path: Path) -> Iterator[DigitalDocument]:
    with zipfile.ZipFile(zip_path) as zf:
        candidates: list[str] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if "Drive/" not in name:
                continue
            if _passes_skip_filters(name):
                candidates.append(name)

        docx_stems = _collect_docx_stems(candidates)

        for fi, name in enumerate(sorted(candidates)):
            if _is_pdf_shadowed_by_docx(name, docx_stems):
                continue
            ext = Path(name).suffix.lower()
            entry = EXTRACTORS.get(ext)
            if not entry:
                continue
            body_source, extractor = entry
            try:
                data = zf.read(name)
            except Exception:
                continue
            info = zf.getinfo(name)
            mtime = datetime(*info.date_time).isoformat() if info.date_time else None
            body = extractor(data)
            if not body or not body.strip():
                continue
            body = body[:MAX_BODY_LEN]
            raw_hash = hashlib.sha256(
                f"google-drive|{name}|{len(body)}".encode()
            ).hexdigest()

            yield DigitalDocument(
                provenance=Provenance(
                    source_path=str(zip_path),
                    raw_hash=raw_hash,
                    source_byte_offset=fi,
                    source_byte_length=len(body),
                ),
                title=Path(name).name,
                body_text=body,
                body_text_source=body_source,
                file_path=name,
                modified_date=mtime,
                bucket=derive_bucket(name),
            )


def _parse_dir(dir_path: Path) -> Iterator[DigitalDocument]:
    candidates: list[str] = []
    for p in sorted(dir_path.rglob("*")):
        if not p.is_file():
            continue
        relstr = str(p.relative_to(dir_path)).replace("\\", "/")
        if _passes_skip_filters(relstr):
            candidates.append(relstr)

    docx_stems = _collect_docx_stems(candidates)

    for fi, relstr in enumerate(sorted(candidates)):
        if _is_pdf_shadowed_by_docx(relstr, docx_stems):
            continue
        ext = Path(relstr).suffix.lower()
        entry = EXTRACTORS.get(ext)
        if not entry:
            continue
        body_source, extractor = entry
        full_path = dir_path / relstr
        try:
            data = full_path.read_bytes()
        except Exception:
            continue
        stat = full_path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
        body = extractor(data)
        if not body or not body.strip():
            continue
        body = body[:MAX_BODY_LEN]
        raw_hash = hashlib.sha256(
            f"google-drive|{relstr}|{len(body)}".encode()
        ).hexdigest()

        yield DigitalDocument(
            provenance=Provenance(
                source_path=str(dir_path),
                raw_hash=raw_hash,
                source_byte_offset=fi,
                source_byte_length=len(body),
            ),
            title=Path(relstr).name,
            body_text=body,
            body_text_source=body_source,
            file_path=relstr,
            file_size=stat.st_size,
            modified_date=mtime,
            bucket=derive_bucket(relstr),
        )
