"""Phone photos directory format parser — yields Photograph records from a camera file tree.

Walks a directory of camera files (jpg, png, mp4, etc.), extracts date from
filename datetime patterns or EXIF fallback, and yields Photograph records.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from phdb.records import Photograph, Provenance

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".3gp", ".m4v", ".mkv"}
INGEST_EXTS = PHOTO_EXTS | VIDEO_EXTS

CONTENT_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".heic": "image/heic",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".3gp": "video/3gpp",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
}

DATETIME_PATTERNS = [
    re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})[.\-](\d{2})[.\-](\d{2})"),
    re.compile(r"(\d{4})-(\d{2})-(\d{2})[-_](\d{2})[-_](\d{2})[-_](\d{2})"),
    re.compile(r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})"),
]


def _parse_filename_dt(name: str) -> str | None:
    """Extract ISO datetime from filename patterns."""
    for pat in DATETIME_PATTERNS:
        m = pat.search(name)
        if m:
            try:
                y, mo, d, h, mi, s = (int(x) for x in m.groups())
                return datetime(y, mo, d, h, mi, s).isoformat()
            except (ValueError, OSError):
                continue
    return None


def _parse_exif_dt(path: Path) -> str | None:
    """Extract DateTimeOriginal from EXIF data."""
    try:
        from PIL import ExifTags, Image  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        img = Image.open(str(path))
        exif = img._getexif()
        if not exif:
            return None
        for tag_id, val in exif.items():
            if ExifTags.TAGS.get(tag_id) == "DateTimeOriginal" and val:
                try:
                    return datetime.strptime(val, "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    return None
        return None
    except Exception:  # noqa: BLE001
        return None


def parse(source_path: Path, *, bucket_label: str | None = None) -> Iterator[Photograph]:
    """Walk a directory tree and yield Photograph records for each media file.

    Args:
        source_path: Root directory containing camera files.
        bucket_label: Optional label for grouping; defaults to sanitized dir name.

    Yields:
        Photograph records with provenance, file metadata, and parsed dates.
    """
    src = source_path.resolve()
    label = bucket_label or re.sub(r"\W+", "-", src.name).strip("-").lower()
    source_str = str(src)

    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in INGEST_EXTS:
            continue

        relpath = str(p.relative_to(src)).replace("\\", "/")
        date_iso = _parse_filename_dt(p.name)
        if not date_iso and ext in PHOTO_EXTS:
            date_iso = _parse_exif_dt(p)

        size = p.stat().st_size
        content_type = CONTENT_TYPES.get(
            ext, mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        )
        raw_hash = hashlib.sha256(f"phone-camera|{relpath}|{size}".encode()).hexdigest()

        yield Photograph(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            file_path=relpath,
            file_name=p.name,
            date_taken=date_iso or "",
            mime_type=content_type,
            file_size=size,
            folder=label,
        )
