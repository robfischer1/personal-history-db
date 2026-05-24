"""PhonePhotosPlugin — Phase 7 brief 031 port of the phone-camera ingester.

Source: a directory of phone-camera files (jpg, png, mp4, etc.) synced
off-device. Walks the tree, yields one ``Photograph`` record per media
file (date parsed from filename datetime patterns, EXIF fallback for
photos), and writes each row to the ``photographs`` typed table
(migration 0016). Sibling to the digikam adapter precedent — same
target table, different source surface (directory walk vs SQLite
query).

Manifest declarations (per spec 031):

- ``emits = ["Photograph"]``
- ``entity_refs = []``
- ``formats_used = ["phone_photos_dir"]``
- ``records_required = ["Photograph"]``
- ``facets_projected = ["Place", "Time", "Person"]``

Replaces the legacy ``phdb.adapters.phone_photos`` module deleted in
the same commit per Phase 0 Q14 (no shim). Reuses the ``photographs``
typed table introduced in migration 0016; no schema changes.

The ``photographs`` INSERT SQL is lifted from
``phdb.adapters.base._INSERT_PHOTOGRAPH_SQL`` so the plugin can run
without inheriting the deprecated ``Adapter`` base class.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.phone_photos_dir import parse as parse_phone_photos
from phdb.log import get_logger
from phdb.records import Photograph

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.phone_photos")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
_INSERT_PHOTOGRAPH_SQL = """\
INSERT OR IGNORE INTO photographs (
    schema_type, source_path, album_root, content_hash,
    captured_at, digitized_at, width, height, format, file_size,
    camera_make, camera_model, lens,
    focal_length, aperture, exposure_time, iso,
    latitude, longitude, altitude, rating,
    source_org, source_kind, provenance,
    raw_hash, source_file_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


class PhonePhotosPlugin(PhdbSourcePlugin):
    """Phone-camera directory plugin — Phase 7 brief 031 port."""

    SOURCE_KIND = "phone-camera"
    FILE_KIND = "directory"
    TARGET_TABLE = "photographs"
    SCHEMA_TYPE = "Photograph"
    PROVENANCE = "filename-datetime"
    BATCH_SIZE = 500

    def __init__(
        self,
        manifest: PluginManifest | None = None,
        *,
        bucket_label: str | None = None,
        target_base_path: str | None = None,
    ) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]
        self.bucket_label = bucket_label
        self.target_base_path = target_base_path

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for the camera root.

        The ``phone_photos_dir`` parser walks the directory itself and
        filters on supported media extensions — discovery here yields
        the directory once so ``parse()`` can hand it to the parser.
        A single file path is also accepted (treated as the root).
        """
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        if root.is_dir():
            yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[Photograph]:
        """Yield Photograph records from one phone-camera directory.

        Delegates to ``phdb.formats.phone_photos_dir.parse`` — the
        directory walk, filename-datetime parsing, EXIF fallback, and
        per-file hash live there.
        """
        bucket_label = self._effective_bucket_label(path)
        yield from parse_phone_photos(path, bucket_label=bucket_label)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: Photograph,
        *,
        source_file_id: int | None = None,
        source_root: Path | None = None,
    ) -> int | None:
        """Insert one Photograph into the ``photographs`` typed table.

        Returns the inserted row id, or ``None`` when the row was a
        dedup-skip (``(source_file_id, raw_hash)`` already present —
        idempotent re-runs).

        ``source_root`` lets the convenience runner pass the original
        source directory so ``source_path`` resolves to a real
        on-disk absolute path. When omitted, the relative path from
        the parser is used as-is (still distinct per file thanks to
        the ``(source_file_id, raw_hash)`` unique index).
        """
        sf_id = source_file_id if source_file_id is not None else 0
        relpath = record.file_path
        size = record.file_size or 0
        content_type = record.mime_type or "application/octet-stream"
        date_iso = record.date_taken or None
        bucket_label = record.folder or ""
        raw_hash = record.provenance.raw_hash

        on_disk_path = self._compute_on_disk_path(relpath, source_root)

        params = (
            self.SCHEMA_TYPE,           # schema_type
            on_disk_path,                # source_path
            bucket_label,                # album_root
            raw_hash,                    # content_hash
            date_iso,                    # captured_at
            None,                        # digitized_at
            record.width,                # width
            record.height,               # height
            content_type,                # format
            size,                        # file_size
            None,                        # camera_make
            record.camera_model,         # camera_model
            None,                        # lens
            None,                        # focal_length
            None,                        # aperture
            None,                        # exposure_time
            None,                        # iso
            record.latitude,             # latitude
            record.longitude,            # longitude
            None,                        # altitude
            None,                        # rating
            self.SOURCE_KIND,            # source_org
            self.FILE_KIND,              # source_kind
            self.PROVENANCE,             # provenance
            raw_hash,                    # raw_hash
            sf_id,                       # source_file_id
        )
        cur = conn.execute(_INSERT_PHOTOGRAPH_SQL, params)
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest phone_photos <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No phone_photos-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one phone-camera directory.

        Mirrors the legacy ``PhonePhotosAdapter.run`` surface
        (inherited from ``Adapter.run``) — the ported tests consume
        this entry point. ``rows_inserted`` / ``rows_skipped`` count
        individual photograph rows; dedup-skips (idempotent re-runs)
        increment ``rows_skipped``.
        """
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        source_root = source_path.resolve() if source_path.is_dir() else None

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(
                conn, record,
                source_file_id=source_file_id,
                source_root=source_root,
            )
            if row_id is None:
                report.rows_skipped += 1
            else:
                report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[phone_photos] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report

    # ----------------------------- Helpers --------------------------------

    def _effective_bucket_label(self, source_path: Path) -> str:
        """Return the bucket_label to hand to the parser.

        Explicit constructor override wins; otherwise the directory
        name is sanitized to a slug (mirrors legacy adapter behavior).
        """
        if self.bucket_label:
            return self.bucket_label
        src = source_path.resolve()
        return re.sub(r"\W+", "-", src.name).strip("-").lower()

    def _compute_on_disk_path(
        self, relpath: str, source_root: Path | None,
    ) -> str:
        """Compose the absolute on-disk path for a photograph row.

        Three branches preserved from the legacy adapter:
        - ``target_base_path`` set: rebase the relpath onto that mount
          (Windows-style separator when ``target_base_path`` looks
          Windows-shaped; POSIX-style otherwise).
        - ``source_root`` provided and Windows-style: rejoin with
          backslashes so the column matches the legacy on-disk
          representation.
        - Otherwise: POSIX-style join under ``source_root`` (or the
          plain relpath when no root is available).
        """
        if self.target_base_path:
            target_base = self.target_base_path.rstrip("\\/")
            sep = (
                "\\"
                if (":" in target_base[:3] or target_base.startswith("\\\\"))
                else "/"
            )
            return target_base + sep + relpath.replace("/", sep)

        if source_root is None:
            return relpath

        src_str = str(source_root)
        if "\\" in src_str:
            return str(source_root / relpath.replace("/", "\\"))
        return str(source_root / relpath)
