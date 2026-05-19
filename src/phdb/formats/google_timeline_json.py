"""Google Timeline location history JSON parser — yields GeoTrace records.

Source: a single locationhistory.json file (post-2024 on-device format).
Three record shapes: visit → Place, activity → TravelAction, timelinePath → GeoShape.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.records import GeoTrace, Provenance

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


def _dedup_hash(kind: str, key: str) -> str:
    return hashlib.sha256(f"google-timeline|{kind}|{key}".encode()).hexdigest()


def _parse_visit(rec: dict[str, object], source_str: str) -> GeoTrace:
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
    lat, lon = _parse_geo(str(place_loc))
    dedup_key = f"{start_ts}|{end_ts}|{place_id}|{place_loc}"

    return GeoTrace(
        provenance=Provenance(
            source_path=source_str,
            raw_hash=_dedup_hash("visit", dedup_key),
        ),
        trace_type="visit",
        date_start=start_ts or "",
        date_end=end_ts,
        latitude=lat,
        longitude=lon,
        place_name=str(sem),
        place_address=str(place_id) if place_id else None,
        activity_type=None,
        confidence=None,
    )


def _parse_activity(rec: dict[str, object], source_str: str) -> GeoTrace:
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
    dedup_key = f"{start_ts}|{end_ts}|{a_type}|{start_loc}|{end_loc}|{distance}"

    return GeoTrace(
        provenance=Provenance(
            source_path=source_str,
            raw_hash=_dedup_hash("activity", dedup_key),
        ),
        trace_type="activity",
        date_start=start_ts or "",
        date_end=end_ts,
        activity_type=str(a_type),
    )


def _parse_timeline_path(
    rec: dict[str, object], source_str: str
) -> GeoTrace:
    points = rec.get("timelinePath") or []
    start_ts = _ts_iso_utc(str(rec.get("startTime", "")))
    end_ts = _ts_iso_utc(str(rec.get("endTime", "")))
    dedup_key = f"{start_ts}|{end_ts}|points={len(points)}"

    waypoints: list[tuple[float, float, str]] = []
    if isinstance(points, list):
        base_epoch: float | None = None
        if start_ts:
            with contextlib.suppress(ValueError):
                base_epoch = datetime.fromisoformat(
                    start_ts.replace("Z", "+00:00")
                ).timestamp()

        for p in points:
            if not isinstance(p, dict):
                continue
            lat, lon = _parse_geo(str(p.get("point", "")))
            if lat is None or lon is None:
                continue
            offset_min = p.get("durationMinutesOffsetFromStartTime")
            ts_v = ""
            if base_epoch is not None and offset_min is not None:
                with contextlib.suppress(TypeError, ValueError):
                    ts_v = datetime.fromtimestamp(
                        base_epoch + float(str(offset_min)) * 60.0, tz=UTC
                    ).isoformat()
            waypoints.append((lat, lon, ts_v))

    return GeoTrace(
        provenance=Provenance(
            source_path=source_str,
            raw_hash=_dedup_hash("timelinepath", dedup_key),
        ),
        trace_type="timelinepath",
        date_start=start_ts or "",
        date_end=end_ts,
        waypoints=tuple(waypoints),
    )


def parse(source_path: Path) -> Iterator[GeoTrace]:
    """Parse Google Timeline JSON, yielding GeoTrace records."""
    import json

    source_str = str(source_path)
    data = json.loads(source_path.read_text(encoding="utf-8"))

    for rec in data:
        if "visit" in rec:
            yield _parse_visit(rec, source_str)
        elif "activity" in rec:
            yield _parse_activity(rec, source_str)
        elif "timelinePath" in rec:
            yield _parse_timeline_path(rec, source_str)
