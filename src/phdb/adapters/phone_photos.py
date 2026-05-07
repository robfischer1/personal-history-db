"""Phone photos adapter — ingests camera files from a directory tree.

Source: a directory of camera files (jpg, png, mp4, etc.).
Each photo/video -> messages row with schema_type='Photograph', is_bulk=1, body_text=None.
Also creates attachments row per file with on_disk_path, content_type, size.
Date parsed from filename datetime patterns or EXIF fallback.
Thread key: phone-camera:{bucket}:{year} where bucket = label from source dir.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.phone_photos")

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


class PhonePhotosAdapter(Adapter):
    """Ingest phone camera photos/videos from a directory tree."""

    name = "phone_photos"
    source_kind = "phone-camera"
    file_kind = "directory"
    schema_type = "Photograph"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def __init__(
        self,
        *,
        bucket_label: str | None = None,
        target_base_path: str | None = None,
    ) -> None:
        self.bucket_label = bucket_label
        self.target_base_path = target_base_path

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        src = source_path.resolve()
        bucket_label = self.bucket_label or re.sub(r"\W+", "-", src.name).strip("-").lower()

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
            content_type = CONTENT_TYPES.get(ext, mimetypes.guess_type(p.name)[0] or "application/octet-stream")

            if self.target_base_path:
                target_base = self.target_base_path.rstrip("\\/")
                sep = "\\" if ":" in target_base[:3] or target_base.startswith("\\\\") else "/"
                on_disk_path = target_base + sep + relpath.replace("/", sep)
            else:
                on_disk_path = str(p)

            raw_hash = hashlib.sha256(f"phone-camera|{relpath}|{size}".encode()).hexdigest()
            year = date_iso[:4] if date_iso else "undated"
            thread_key = f"phone-camera:{bucket_label}:{year}"

            yield AdapterRow(
                schema_type="Photograph",
                rfc822_message_id=f"phone-camera:{raw_hash}",
                subject=p.name[:200],
                sender_address="phone-camera:rob",
                sender_name="phone-camera",
                direction="self",
                date_sent=date_iso,
                body_text=None,
                body_text_source="photo-metadata",
                has_attachments=1,
                attachment_count=1,
                is_bulk=1,
                bulk_signal="photograph-no-body",
                raw_hash=raw_hash,
                thread_key=thread_key,
                attachments=[
                    {
                        "filename": p.name,
                        "content_type": content_type,
                        "content_disposition": "inline",
                        "size_bytes": size,
                        "on_disk_path": on_disk_path,
                        "content_hash": raw_hash,
                    }
                ],
            )
