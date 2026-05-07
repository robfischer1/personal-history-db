"""Spotify adapter — ingests Spotify Extended Streaming History.

Source: a zip or directory containing ``Streaming_History_Audio_*.json``
and ``Streaming_History_Video_*.json`` files.

Each play event becomes a schema_type='ListenAction' row with is_bulk=1
(skip embedding — track names aren't narrative text). All events bucket
into a single thread.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.spotify")

_MAX_BODY_LEN = 2000


def _yield_streaming_files(source_path: Path) -> Iterator[tuple[str, bytes]]:
    if source_path.is_file() and source_path.suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if "Streaming_History_" in name and name.endswith(".json"):
                    yield name, zf.read(name)
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("Streaming_History_*.json")):
            yield str(p.relative_to(source_path)), p.read_bytes()
        for zp in sorted(source_path.glob("*.zip")):
            with zipfile.ZipFile(zp) as zf:
                for name in sorted(zf.namelist()):
                    if "Streaming_History_" in name and name.endswith(".json"):
                        yield f"{zp.name}!{name}", zf.read(name)


def _parse_event(evt: dict[str, object], file_idx: int, evt_idx: int) -> AdapterRow | None:
    ts = evt.get("ts")
    if not ts:
        return None

    track = str(evt.get("master_metadata_track_name") or "")
    artist = str(evt.get("master_metadata_album_artist_name") or "")
    album = str(evt.get("master_metadata_album_album_name") or "")
    episode = evt.get("episode_name")
    show = evt.get("episode_show_name")
    audiobook = evt.get("audiobook_title")
    chapter = evt.get("audiobook_chapter_title")

    if track:
        subject = f"{track} — {artist}" if artist else track
        body_parts = [track]
        if artist:
            body_parts.append(f"by {artist}")
        if album:
            body_parts.append(f"({album})")
        body_text = " ".join(body_parts)
        sender_name = artist or track
    elif episode:
        subject = f"{episode} — {show}" if show else str(episode)
        body_text = f"Podcast: {episode}" + (f" ({show})" if show else "")
        sender_name = str(show or episode)
    elif audiobook:
        subject = f"{audiobook} — {chapter}" if chapter else str(audiobook)
        body_text = f"Audiobook: {audiobook}" + (f" — {chapter}" if chapter else "")
        sender_name = str(audiobook)
    else:
        return None

    if len(body_text) > _MAX_BODY_LEN:
        body_text = body_text[:_MAX_BODY_LEN]

    uri = str(
        evt.get("spotify_track_uri")
        or evt.get("spotify_episode_uri")
        or evt.get("audiobook_uri")
        or track or episode or audiobook
    )
    dedup_seed = f"spotify|{ts}|{uri}|{evt.get('ms_played')}"
    raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

    return AdapterRow(
        schema_type="ListenAction",
        rfc822_message_id=f"spotify:{raw_hash}",
        subject=subject,
        sender_address="spotify:self",
        sender_name=sender_name,
        direction="self",
        date_sent=str(ts),
        body_text=body_text,
        body_text_source="spotify-json",
        is_bulk=1,
        bulk_signal="spotify-listen-event",
        source_byte_offset=file_idx,
        source_byte_length=evt_idx,
        raw_hash=raw_hash,
        body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
        thread_key="spotify:listening",
    )


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
        for fi, (_relpath, json_bytes) in enumerate(_yield_streaming_files(source_path)):
            try:
                data = json.loads(json_bytes)
            except json.JSONDecodeError:
                continue
            events = data if isinstance(data, list) else [data]
            for ei, evt in enumerate(events):
                row = _parse_event(evt, fi, ei)
                if row:
                    yield row

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

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        for tid in touched_threads:
            self._update_thread_aggregates(conn, tid)

        actual = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (actual, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
