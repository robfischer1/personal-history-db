"""Google Timeline adapter — ingests post-2024 on-device location history JSON.

Source: a single locationhistory.json file.
Three record shapes: visit->Place, activity->TravelAction, timelinePath->GeoShape.
Geo trace points go to the geo_traces sidecar table.
Single thread: google-timeline:lifestream. All is_bulk=1.
Custom run() for geo_traces writes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.google_timeline")

_MAX_BODY_LEN = 2000
_GEO_RE = re.compile(r"^geo:(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$")


def _parse_geo(s: str) -> tuple[float | None, float | None]:
    if not s:
        return None, None
    m = _GEO_RE.match(s.strip())
    if not m:
        return None, None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None, None


def _ts_iso_utc(s: str | None) -> str | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return dt.astimezone(UTC).isoformat()
    except ValueError:
        return s


def _make_row(
    schema_type: str,
    dedup_kind: str,
    dedup_key: str,
    subject: str,
    body_text: str,
    start_ts: str | None,
    end_ts: str | None,
    bulk_signal: str,
) -> AdapterRow:
    body_text = body_text[:_MAX_BODY_LEN]
    raw_hash = hashlib.sha256(f"google-timeline|{dedup_kind}|{dedup_key}".encode()).hexdigest()
    return AdapterRow(
        schema_type=schema_type,
        rfc822_message_id=f"google-timeline:{dedup_kind[:3]}:{raw_hash[:16]}",
        subject=subject,
        sender_address="google-timeline:self",
        sender_name="google-timeline",
        direction="self",
        date_sent=start_ts,
        date_received=end_ts,
        body_text=body_text,
        body_text_source="google-timeline-json",
        is_bulk=1,
        bulk_signal=bulk_signal,
        raw_hash=raw_hash,
        body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
        thread_key="google-timeline:lifestream",
    )


def _visit_row(rec: dict[str, object]) -> AdapterRow:
    visit = rec.get("visit") or {}
    if not isinstance(visit, dict):
        visit = {}
    tc = visit.get("topCandidate") or {}
    if not isinstance(tc, dict):
        tc = {}
    sem = tc.get("semanticType") or "Unknown"
    place_id = tc.get("placeID") or ""
    place_loc = tc.get("placeLocation") or ""
    start_ts = _ts_iso_utc(str(rec.get("startTime", "")))
    end_ts = _ts_iso_utc(str(rec.get("endTime", "")))
    body = f"Visit ({sem}) place_id={place_id} loc={place_loc} start={start_ts} end={end_ts}"
    dedup_key = f"{start_ts}|{end_ts}|{place_id}|{place_loc}"
    return _make_row("Place", "visit", dedup_key, f"Visit: {sem}", body, start_ts, end_ts, "google-timeline-visit")


def _activity_row(rec: dict[str, object]) -> AdapterRow:
    act = rec.get("activity") or {}
    if not isinstance(act, dict):
        act = {}
    tc = act.get("topCandidate") or {}
    if not isinstance(tc, dict):
        tc = {}
    a_type = tc.get("type") or "unknown"
    start_loc = act.get("start") or ""
    end_loc = act.get("end") or ""
    distance = act.get("distanceMeters") or ""
    start_ts = _ts_iso_utc(str(rec.get("startTime", "")))
    end_ts = _ts_iso_utc(str(rec.get("endTime", "")))
    body = f"Activity ({a_type}) start={start_loc} end={end_loc} distance={distance}m start_ts={start_ts} end_ts={end_ts}"
    dedup_key = f"{start_ts}|{end_ts}|{a_type}|{start_loc}|{end_loc}|{distance}"
    return _make_row("TravelAction", "activity", dedup_key, f"Activity: {a_type}", body, start_ts, end_ts, "google-timeline-activity")


class GoogleTimelineAdapter(Adapter):
    """Ingest Google Timeline location history JSON."""

    name = "google_timeline"
    source_kind = "google-timeline"
    file_kind = "json"
    schema_type = "Place"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 1000

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly — geo_traces needs conn access")

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

        data = json.loads(source_path.read_text(encoding="utf-8"))
        log.info("[%s] Loaded %d top-level records", self.name, len(data))

        touched_threads: set[int] = set()
        batch_count = 0

        for rec in data:
            row: AdapterRow | None = None
            geo_points: list[dict[str, object]] | None = None

            if "visit" in rec:
                row = _visit_row(rec)
            elif "activity" in rec:
                row = _activity_row(rec)
            elif "timelinePath" in rec:
                points = rec.get("timelinePath") or []
                start_ts = _ts_iso_utc(str(rec.get("startTime", "")))
                end_ts = _ts_iso_utc(str(rec.get("endTime", "")))
                body = f"timelinePath {len(points)} points start={start_ts} end={end_ts}"
                dedup_key = f"{start_ts}|{end_ts}|points={len(points)}"
                row = _make_row(
                    "GeoShape", "timelinepath", dedup_key,
                    f"Trace: {len(points)} points", body, start_ts, end_ts,
                    "google-timeline-path",
                )
                if isinstance(points, list):
                    geo_points = points
            else:
                continue

            report.rows_yielded += 1
            message_id = self._insert_row(conn, row, source_file_id)
            if message_id is None:
                report.rows_skipped += 1
                continue

            report.rows_inserted += 1

            if row.thread_key:
                thread_id, created = self._upsert_thread(conn, row.thread_key)
                self._link_message_thread(conn, message_id, thread_id)
                if created:
                    report.threads_created += 1
                touched_threads.add(thread_id)

            if geo_points and message_id:
                base_epoch = None
                if row.date_sent:
                    with contextlib.suppress(ValueError):
                        base_epoch = datetime.fromisoformat(
                            row.date_sent.replace("Z", "+00:00")
                        ).timestamp()

                for idx, p in enumerate(geo_points):
                    if not isinstance(p, dict):
                        continue
                    lat, lon = _parse_geo(str(p.get("point", "")))
                    if lat is None or lon is None:
                        continue
                    offset_min = p.get("durationMinutesOffsetFromStartTime")
                    ts_v = None
                    if base_epoch is not None and offset_min is not None:
                        with contextlib.suppress(TypeError, ValueError):
                            ts_v = datetime.fromtimestamp(
                                base_epoch + float(str(offset_min)) * 60.0, tz=UTC
                            ).isoformat()
                    conn.execute(
                        """INSERT INTO geo_traces
                              (parent_message_id, source_kind, point_idx, ts, lat, lon,
                               extra_json)
                           VALUES (?, 'google-timeline-path', ?, ?, ?, ?, ?)""",
                        (message_id, idx, ts_v, lat, lon, json.dumps({"offset_min": offset_min})),
                    )

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        for tid in touched_threads:
            self._update_thread_aggregates(conn, tid)
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
