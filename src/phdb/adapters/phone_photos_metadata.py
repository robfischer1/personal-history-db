"""Phone photos metadata adapter — ingests Android MediaStore SQLite from tar.gz.

Source: tar.gz containing external-61396137.db (Android MediaStore).
Each image row -> messages with schema_type='Photograph', is_bulk=1.
body_text = synthetic metadata string (folder, path, mime, bytes, gps).
Dedup: rfc822_message_id = 'phone-photo-meta:{sha256(path)}'.
Thread key: phone-camera:{folder}:{year}.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.phone_photos_metadata")

INNER_DB_NAME = "external-61396137.db"


def _best_iso(
    datetaken: int | None,
    date_added: int | None,
    date_modified: int | None,
) -> str | None:
    if datetaken:
        try:
            return datetime.fromtimestamp(int(datetaken) / 1000, tz=UTC).isoformat()
        except (ValueError, OSError, OverflowError):
            pass
    if date_added:
        try:
            return datetime.fromtimestamp(int(date_added), tz=UTC).isoformat()
        except (ValueError, OSError, OverflowError):
            pass
    if date_modified:
        try:
            return datetime.fromtimestamp(int(date_modified), tz=UTC).isoformat()
        except (ValueError, OSError, OverflowError):
            pass
    return None


def _folder_label(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if len(parts) >= 2:
        return parts[-2]
    return "unsorted"


class PhonePhotosMetadataAdapter(Adapter):
    """Ingest photo metadata from Android MediaStore SQLite in tar.gz."""

    name = "phone_photos_metadata"
    source_kind = "phone-camera"
    file_kind = "android-mediastore"
    schema_type = "Photograph"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        tmpdir = Path(tempfile.mkdtemp(prefix="media-tb-"))
        try:
            db_path = self._extract_db(source_path, tmpdir)
            if db_path is None:
                log.warning("[%s] %s not found inside %s", self.name, INNER_DB_NAME, source_path)
                return

            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            src.row_factory = sqlite3.Row
            try:
                for r in src.execute(
                    """SELECT _data, _display_name, _size, mime_type,
                              date_added, date_modified, datetaken,
                              latitude, longitude, orientation
                         FROM images"""
                ):
                    row = self._make_row(r)
                    if row is not None:
                        yield row
            finally:
                src.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _extract_db(self, tar_path: Path, tmpdir: Path) -> Path | None:
        with tarfile.open(str(tar_path), "r:gz") as tf:
            for m in tf.getmembers():
                if m.name.endswith(f"/{INNER_DB_NAME}"):
                    tf.extract(m, tmpdir)
                    return tmpdir / m.name
        return None

    def _make_row(self, r: sqlite3.Row) -> AdapterRow | None:
        path: str = r["_data"] or ""
        if not path:
            return None

        fname = Path(path.replace("\\", "/")).name or "(unnamed)"
        msg_id_key = f"phone-photo-meta:{hashlib.sha256(path.encode()).hexdigest()}"
        raw_hash = hashlib.sha256(msg_id_key.encode()).hexdigest()

        date_iso = _best_iso(r["datetaken"], r["date_added"], r["date_modified"])
        bucket = _folder_label(path)
        year = date_iso[:4] if date_iso else "undated"

        bits = [
            f"folder={bucket}",
            f"path={path}",
            f"mime={r['mime_type'] or ''}",
            f"bytes={r['_size'] or 0}",
        ]
        if r["latitude"] not in (None, 0) or r["longitude"] not in (None, 0):
            bits.append(f"gps=({r['latitude']},{r['longitude']})")
        if r["orientation"]:
            bits.append(f"orientation={r['orientation']}")
        body = " | ".join(bits)
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        return AdapterRow(
            schema_type="Photograph",
            rfc822_message_id=msg_id_key,
            subject=fname[:200],
            sender_address="phone-camera:rob",
            sender_name="phone-camera",
            direction="self",
            date_sent=date_iso,
            body_text=body,
            body_text_source="phone-photo-metadata-only",
            is_bulk=1,
            bulk_signal="photograph-metadata-only",
            raw_hash=raw_hash,
            body_text_hash=body_hash,
            thread_key=f"phone-camera:{bucket}:{year}",
        )
