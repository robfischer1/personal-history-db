"""Google Timeline adapter — ingests post-2024 on-device location history JSON.

Consumes GeoTrace records from phdb.formats.google_timeline_json.
Three record shapes: visit->Place, activity->TravelAction, timelinePath->GeoShape.
Geo trace points go to the geo_traces sidecar table via declared sidecar API.
Single thread: google-timeline:lifestream. All is_bulk=1.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import (
    Adapter,
    AdapterRow,
    DedupStrategy,
    SidecarColumn,
    SidecarTableDef,
)
from phdb.formats.google_timeline_json import parse
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_timeline")

_MAX_BODY_LEN = 2000

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

_TRACE_TYPE_TO_SCHEMA: dict[str, tuple[str, str]] = {
    "visit": ("Place", "google-timeline-visit"),
    "activity": ("TravelAction", "google-timeline-activity"),
    "timelinepath": ("GeoShape", "google-timeline-path"),
}


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
        for rec in parse(source_path):
            schema_type, bulk_signal = _TRACE_TYPE_TO_SCHEMA.get(
                rec.trace_type, ("Place", "google-timeline-unknown")
            )

            if rec.trace_type == "visit":
                subject = f"Visit: {rec.place_name or 'Unknown'}"
                body = (
                    f"Visit ({rec.place_name}) place_id={rec.place_address or ''} "
                    f"loc= start={rec.date_start} end={rec.date_end}"
                )
            elif rec.trace_type == "activity":
                subject = f"Activity: {rec.activity_type or 'unknown'}"
                body = (
                    f"Activity ({rec.activity_type}) "
                    f"start_ts={rec.date_start} end_ts={rec.date_end}"
                )
            else:
                subject = f"Trace: {len(rec.waypoints)} points"
                body = (
                    f"timelinePath {len(rec.waypoints)} points "
                    f"start={rec.date_start} end={rec.date_end}"
                )

            body = body[:_MAX_BODY_LEN]
            raw_hash = rec.provenance.raw_hash

            row = AdapterRow(
                schema_type=schema_type,
                rfc822_message_id=f"google-timeline:{rec.trace_type[:3]}:{raw_hash[:16]}",
                subject=subject,
                sender_address="google-timeline:self",
                sender_name="google-timeline",
                direction="self",
                date_sent=rec.date_start or None,
                date_received=rec.date_end,
                body_text=body,
                body_text_source="google-timeline-json",
                is_bulk=1,
                bulk_signal=bulk_signal,
                raw_hash=raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                thread_key="google-timeline:lifestream",
            )

            if rec.waypoints:
                geo_rows: list[dict[str, object]] = []
                for idx, (lat, lon, ts_v) in enumerate(rec.waypoints):
                    geo_rows.append({
                        "source_kind": "google-timeline-path",
                        "point_idx": idx,
                        "ts": ts_v or None,
                        "lat": lat,
                        "lon": lon,
                        "elevation_m": None,
                        "speed_mps": None,
                        "course": None,
                        "horizontal_accuracy_m": None,
                        "vertical_accuracy_m": None,
                        "extra_json": None,
                    })
                row.sidecar_rows["geo_traces"] = geo_rows

            yield row
