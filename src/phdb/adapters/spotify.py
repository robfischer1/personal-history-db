"""Spotify adapter — ingests Spotify Extended Streaming History.

Consumes MediaPlay records from phdb.formats.spotify_json.

Each play event becomes a schema_type='ListenAction' row with is_bulk=1
(skip embedding — track names aren't narrative text). All events bucket
into a single thread.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.formats.spotify_json import parse
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.spotify")

_MAX_BODY_LEN = 2000


class SpotifyAdapter(Adapter):
    """Ingest Spotify Extended Streaming History."""

    name = "spotify"
    source_kind = "spotify"
    file_kind = "json"
    schema_type = "ListenAction"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 1000

    def __init__(self, *, max_seconds: float | None = None) -> None:
        self.max_seconds = max_seconds

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse(source_path):
            body_parts = [rec.title]
            if rec.artist and rec.media_type == "music":
                body_parts = [rec.title.split(" — ")[0]]
                if rec.artist:
                    body_parts.append(f"by {rec.artist}")
                if rec.album:
                    body_parts.append(f"({rec.album})")
            elif rec.media_type == "podcast":
                body_parts = [f"Podcast: {rec.title}"]
            elif rec.media_type == "audiobook":
                body_parts = [f"Audiobook: {rec.title}"]

            body_text = " ".join(body_parts)
            if len(body_text) > _MAX_BODY_LEN:
                body_text = body_text[:_MAX_BODY_LEN]

            sender_name = rec.artist or rec.title

            yield AdapterRow(
                schema_type="ListenAction",
                rfc822_message_id=f"spotify:{rec.provenance.raw_hash}",
                subject=rec.title,
                sender_address="spotify:self",
                sender_name=sender_name,
                direction="self",
                date_sent=rec.date_played,
                body_text=body_text,
                body_text_source="spotify-json",
                is_bulk=1,
                bulk_signal="spotify-listen-event",
                raw_hash=rec.provenance.raw_hash,
                body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
                thread_key="spotify:listening",
            )

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        return True, "spotify-listen-event"

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
        log.info("[%s] Source registered: id=%d path=%s", self.name, source_file_id, source_path)

        t_start = time.time()
        touched_threads: set[int] = set()
        thread_dates: dict[int, tuple[str, str]] = {}
        batch_count = 0

        for row in self.iter_rows(source_path):
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                break

            report.rows_yielded += 1

            if not row.raw_hash:
                row.raw_hash = self.compute_raw_hash(row)
            if row.body_text and not row.body_text_hash:
                row.body_text_hash = hashlib.sha256(row.body_text.encode()).hexdigest()

            message_id = self._insert_row(conn, row, source_file_id)
            if message_id is None:
                report.rows_skipped += 1
                continue

            report.rows_inserted += 1
            self._insert_sidecars(conn, message_id, row)

            if row.thread_key:
                thread_id, created = self._upsert_thread(conn, row.thread_key)
                self._link_message_thread(conn, message_id, thread_id)
                if created:
                    report.threads_created += 1
                touched_threads.add(thread_id)
                rd = row.date_sent
                if rd and thread_id in thread_dates:
                    lo, hi = thread_dates[thread_id]
                    thread_dates[thread_id] = (min(lo, rd), max(hi, rd))
                elif rd:
                    thread_dates[thread_id] = (rd, rd)

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        for tid in touched_threads:
            dates = thread_dates.get(tid)
            self._update_thread_aggregates(
                conn, tid,
                dates[0] if dates else None,
                dates[1] if dates else None,
            )

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
