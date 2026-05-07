"""TitaniumBackup Android browser bookmarks adapter.

Source: tar.gz containing dbdata/databases/com.android.browser/browser.db.
Writes to the `bookmarks` table (not messages). Custom run() override.
Two instrument values based on the bookmark flag:
  bookmark=1 -> 'com-android-browser-bookmark'
  bookmark=0 -> 'com-android-browser-history'

URL normalization reuses raindrop's normalize_url + is_junk.
Dedup: INSERT OR IGNORE on UNIQUE(normalized_url, instrument).
"""

from __future__ import annotations

import hashlib
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.adapters.raindrop import is_junk, normalize_url
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.titaniumbackup_browser_bookmarks")

INSTRUMENT_BOOKMARK = "com-android-browser-bookmark"
INSTRUMENT_HISTORY = "com-android-browser-history"
INNER_DB_NAME = "browser.db"


def _date_iso(ms: object) -> str | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(str(ms)) / 1000, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


class TitaniumBackupBrowserBookmarksAdapter(Adapter):
    """Ingest Android stock-browser bookmarks + history from TitaniumBackup tarballs."""

    name = "titaniumbackup_browser_bookmarks"
    source_kind = "titaniumbackup-android-browser"
    file_kind = "tar.gz"
    schema_type = "BookmarkAction"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly -- writes to bookmarks table")

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id

        tmpdir = Path(tempfile.mkdtemp(prefix="browser-tb-"))
        db_path: Path | None = None
        try:
            with tarfile.open(source_path, "r:gz") as tf:
                for m in tf.getmembers():
                    if m.name.endswith(f"/{INNER_DB_NAME}") and "com.android.browser" in m.name:
                        tf.extract(m, tmpdir)
                        db_path = tmpdir / m.name
                        break

            if db_path is None or not db_path.exists():
                report.errors.append(f"{INNER_DB_NAME} not found inside {source_path}")
                return report

            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            src.row_factory = sqlite3.Row
            try:
                rows = src.execute(
                    """SELECT _id, title, url, visits, date, created,
                              description, bookmark
                         FROM bookmarks"""
                ).fetchall()
            except sqlite3.OperationalError as e:
                report.errors.append(f"Could not read browser.db: {e}")
                src.close()
                return report

            batch_count = 0
            for r in rows:
                report.rows_yielded += 1
                raw_url = (r["url"] or "").strip()
                if not raw_url:
                    report.rows_skipped += 1
                    continue

                junk = is_junk(raw_url)
                if junk:
                    report.rows_skipped += 1
                    continue

                norm = normalize_url(raw_url)
                if not norm:
                    report.rows_skipped += 1
                    continue

                instrument = INSTRUMENT_BOOKMARK if (r["bookmark"] or 0) == 1 else INSTRUMENT_HISTORY
                title = (r["title"] or "").strip() or None
                created = _date_iso(r["created"])
                last_seen = _date_iso(r["date"])
                favorite = 1 if instrument == INSTRUMENT_BOOKMARK else 0
                raw_hash = hashlib.sha256(f"{instrument}|{norm}".encode()).hexdigest()

                cur = conn.execute(
                    """INSERT OR IGNORE INTO bookmarks
                          (schema_type, instrument, url, normalized_url, raindrop_id,
                           title, note, excerpt, cover_url, folder, tags, favorite,
                           highlights, first_seen_in_instrument, last_seen_in_instrument,
                           raindrop_created, appearance_count, excluded, excluded_reason,
                           source_file_id, raw_hash)
                       VALUES ('BookmarkAction', ?, ?, ?, NULL,
                               ?, NULL, NULL, NULL, NULL, NULL, ?,
                               NULL, ?, ?,
                               ?, 1, 0, NULL,
                               ?, ?)""",
                    (instrument, raw_url, norm, title, favorite,
                     created or last_seen, last_seen or created,
                     created,
                     source_file_id, raw_hash),
                )
                if cur.rowcount == 0:
                    report.rows_skipped += 1
                    continue
                report.rows_inserted += 1

                batch_count += 1
                if batch_count >= self.batch_size:
                    conn.commit()
                    batch_count = 0

            src.close()
        finally:
            try:
                for p in sorted(tmpdir.rglob("*"), reverse=True):
                    if p.is_file() or p.is_symlink():
                        p.unlink()
                    elif p.is_dir():
                        p.rmdir()
                tmpdir.rmdir()
            except Exception:
                pass

        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
