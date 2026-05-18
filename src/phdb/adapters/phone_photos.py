"""Phone photos adapter — ingests camera files from a directory tree.

Source: a directory of camera files (jpg, png, mp4, etc.).
Each photo/video -> messages row with schema_type='Photograph', is_bulk=1, body_text=None.
Also creates attachments row per file with on_disk_path, content_type, size.
Date parsed from filename datetime patterns or EXIF fallback.
Thread key: phone-camera:{bucket}:{year} where bucket = label from source dir.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.phone_photos_dir import (
    CONTENT_TYPES,
    DATETIME_PATTERNS,
    INGEST_EXTS,
    PHOTO_EXTS,
    VIDEO_EXTS,
    _parse_exif_dt,
    _parse_filename_dt,
    parse,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.phone_photos")

# Re-export for backward compatibility with tests
__all__ = [
    "CONTENT_TYPES",
    "DATETIME_PATTERNS",
    "INGEST_EXTS",
    "PHOTO_EXTS",
    "PhonePhotosAdapter",
    "VIDEO_EXTS",
    "_parse_exif_dt",
    "_parse_filename_dt",
]


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

        for photo in parse(source_path, bucket_label=bucket_label):
            relpath = photo.file_path
            size = photo.file_size or 0
            content_type = photo.mime_type or "application/octet-stream"
            date_iso = photo.date_taken or None

            if self.target_base_path:
                target_base = self.target_base_path.rstrip("\\/")
                sep = "\\" if ":" in target_base[:3] or target_base.startswith("\\\\") else "/"
                on_disk_path = target_base + sep + relpath.replace("/", sep)
            else:
                on_disk_path = str(src / relpath.replace("/", "\\")) if "\\" in str(src) else str(src / relpath)

            raw_hash = photo.provenance.raw_hash
            year = date_iso[:4] if date_iso else "undated"
            thread_key = f"phone-camera:{bucket_label}:{year}"

            yield AdapterRow(
                schema_type="Photograph",
                rfc822_message_id=f"phone-camera:{raw_hash}",
                subject=photo.file_name[:200],
                sender_address=self.owner_sender("phone-camera")[0],
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
                        "filename": photo.file_name,
                        "content_type": content_type,
                        "content_disposition": "inline",
                        "size_bytes": size,
                        "on_disk_path": on_disk_path,
                        "content_hash": raw_hash,
                    }
                ],
            )
