"""Google Timeline adapter — ingests post-2024 on-device location history JSON.

Source: a single locationhistory.json file.
Three record shapes: visit->Place, activity->TravelAction, timelinePath->GeoShape.
Geo trace points go to the geo_traces sidecar table via declared sidecar API.
Single thread: google-timeline:lifestream. All is_bulk=1.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.adapters.base import (
    Adapter,
    AdapterRow,
    DedupStrategy,
    SidecarColumn,
    SidecarTableDef,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_timeline")

_MAX_BODY_LEN = 2000
_GEO_RE = re.compile(r"^geo:(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$")

GEO_TRACES_TABLE = SidecarTableDef(
    table_name="geo_traces",
    columns=(
        SidecarColumn("source_kind", "TEXT", nullable=False),
        SidecarColumn("point_idx", "INTEGER", nullable=False),
        SidecarColumn("ts", "TEXT"),
        SidecarColumn("lat", "REAL", nullable=False),
        SidecarColumn("lon", "REAL", nullable=False),
        SidecarColumn("elevation_m", "REAL"),
        SidecarColumn("speed_mps", "REAL"),
        SidecarColumn("course", "REAL"),
        SidecarColumn("horizontal_accuracy_m", "REAL"),
        SidecarColumn("vertical_accuracy_m", "REAL"),
        SidecarColumn("extra_json", "TEXT"),
    ),
    parent_fk_column="parent_message_id",
    parent_table="messages",
)


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
    sidecar_tables = [GEO_TRACES_TABLE]

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        data = json.loads(source_path.read_text(encoding="utf-8"))
        log.info("[%s] Loaded %d top-level records", self.name, len(data))

        for rec in data:
            if "visit" in rec:
                yield _visit_row(rec)
            elif "activity" in rec:
                yield _activity_row(rec)
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
                    geo_rows = self._build_geo_rows(points, start_ts)
                    if geo_rows:
                        row.sidecar_rows["geo_traces"] = geo_rows

                yield row

    def _build_geo_rows(
        self, points: list[object], start_ts: str | None
    ) -> list[dict[str, object]]:
        base_epoch: float | None = None
        if start_ts:
            with contextlib.suppress(ValueError):
                base_epoch = datetime.fromisoformat(
                    start_ts.replace("Z", "+00:00")
                ).timestamp()

        geo_rows: list[dict[str, object]] = []
        for idx, p in enumerate(points):
            if not isinstance(p, dict):
                continue
            lat, lon = _parse_geo(str(p.get("point", "")))
            if lat is None or lon is None:
                continue
            offset_min = p.get("durationMinutesOffsetFromStartTime")
            ts_v: str | None = None
            if base_epoch is not None and offset_min is not None:
                with contextlib.suppress(TypeError, ValueError):
                    ts_v = datetime.fromtimestamp(
                        base_epoch + float(str(offset_min)) * 60.0, tz=UTC
                    ).isoformat()
            geo_rows.append({
                "source_kind": "google-timeline-path",
                "point_idx": idx,
                "ts": ts_v,
                "lat": lat,
                "lon": lon,
                "elevation_m": None,
                "speed_mps": None,
                "course": None,
                "horizontal_accuracy_m": None,
                "vertical_accuracy_m": None,
                "extra_json": json.dumps({"offset_min": offset_min}),
            })
        return geo_rows
