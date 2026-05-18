"""Apple Health adapter — ingests Health_Export.zip via streaming XML.

Source: Health_Export.zip containing apple_health_export/export.xml + GPX routes.
Three record types mapped to messages + 5 sidecar tables:
  Record      -> Observation   + record_metadata + hr_samples
  Workout     -> ExerciseAction + workout_events + workout_statistics + geo_traces
  ClinicalRecord -> MedicalRecord (no sidecars)

Custom run() required for sidecar table writes.
Memory-flat: iterparse with periodic root.clear() and commit cadence.
"""

from __future__ import annotations

import hashlib
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING

from phdb.adapters.base import (
    Adapter,
    AdapterRow,
    DedupStrategy,
    IngestReport,
    SidecarColumn,
    SidecarTableDef,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.apple_health")

MAX_BODY_LEN = 2000
COMMIT_EVERY = 25000
ROOT_CLEAR_EVERY = 10000

HK_PREFIXES = (
    "HKQuantityTypeIdentifier",
    "HKCategoryTypeIdentifier",
    "HKWorkoutActivityType",
    "HKDataType",
)


def _strip_hk_prefix(s: str) -> str:
    if not s:
        return s
    for p in HK_PREFIXES:
        if s.startswith(p):
            return s[len(p) :]
    return s


def _parse_apple_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s.replace("Z", "+0000") if fmt.endswith("Z") else s, fmt)
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            continue
    return s


def _safe_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


RECORD_METADATA_TABLE = SidecarTableDef(
    table_name="record_metadata",
    columns=(
        SidecarColumn("key", "TEXT", nullable=False),
        SidecarColumn("value", "TEXT"),
    ),
    parent_fk_column="message_id",
    parent_table="messages",
)

HR_SAMPLES_TABLE = SidecarTableDef(
    table_name="hr_samples",
    columns=(
        SidecarColumn("ts", "TEXT", nullable=False),
        SidecarColumn("bpm", "INTEGER", nullable=False),
    ),
    parent_fk_column="parent_message_id",
    parent_table="messages",
)

WORKOUT_EVENTS_TABLE = SidecarTableDef(
    table_name="workout_events",
    columns=(
        SidecarColumn("event_type", "TEXT"),
        SidecarColumn("date", "TEXT"),
        SidecarColumn("duration_seconds", "REAL"),
    ),
    parent_fk_column="workout_message_id",
    parent_table="messages",
)

WORKOUT_STATISTICS_TABLE = SidecarTableDef(
    table_name="workout_statistics",
    columns=(
        SidecarColumn("stat_type", "TEXT", nullable=False),
        SidecarColumn("value_min", "REAL"),
        SidecarColumn("value_avg", "REAL"),
        SidecarColumn("value_max", "REAL"),
        SidecarColumn("value_sum", "REAL"),
        SidecarColumn("unit", "TEXT"),
        SidecarColumn("date_start", "TEXT"),
        SidecarColumn("date_end", "TEXT"),
    ),
    parent_fk_column="workout_message_id",
    parent_table="messages",
)

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


class AppleHealthAdapter(Adapter):
    """Ingest Apple Health Export zip (streaming XML + GPX)."""

    name = "apple_health"
    source_kind = "apple-health"
    file_kind = "zip"
    schema_type = "Observation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 25000
    sidecar_tables = [
        RECORD_METADATA_TABLE,
        HR_SAMPLES_TABLE,
        WORKOUT_EVENTS_TABLE,
        WORKOUT_STATISTICS_TABLE,
        GEO_TRACES_TABLE,
    ]

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly — sidecar tables need conn access")

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

        self.ensure_sidecar_tables(conn)
        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id

        metrics_thread_id, metrics_created = self._upsert_thread(conn, "apple-health:metrics")
        clinical_thread_id, clinical_created = self._upsert_thread(conn, "apple-health:clinical")
        if metrics_created:
            report.threads_created += 1
        if clinical_created:
            report.threads_created += 1

        touched_threads: set[int] = {metrics_thread_id, clinical_thread_id}
        processed = 0

        with zipfile.ZipFile(source_path) as zf, zf.open("apple_health_export/export.xml") as f:
            context = ET.iterparse(f, events=("start", "end"))
            _event, root = next(context)

            for event, elem in context:
                if event != "end":
                    continue

                tag = elem.tag
                if tag == "Record":
                    self._handle_record(
                        conn, elem, source_file_id, metrics_thread_id, report,
                    )
                elif tag == "Workout":
                    wt_threads = self._handle_workout(
                        conn, elem, source_file_id, zf, report,
                    )
                    touched_threads.update(wt_threads)
                elif tag == "ClinicalRecord":
                    self._handle_clinical(
                        conn, elem, source_file_id, clinical_thread_id, report,
                    )
                else:
                    continue

                elem.clear()
                processed += 1

                if processed % ROOT_CLEAR_EVERY == 0:
                    root.clear()
                if processed % COMMIT_EVERY == 0:
                    conn.commit()

        conn.commit()

        for tid in touched_threads:
            self._update_thread_aggregates(conn, tid)
        conn.commit()

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name,
            report.rows_yielded,
            report.rows_inserted,
            report.rows_skipped,
        )
        return report

    def _handle_record(
        self,
        conn: sqlite3.Connection,
        elem: ET.Element,
        source_file_id: int,
        metrics_thread_id: int,
        report: IngestReport,
    ) -> None:
        rtype = elem.get("type", "")
        rtype_label = _strip_hk_prefix(rtype)
        unit = elem.get("unit", "")
        value = elem.get("value", "")
        source_name = elem.get("sourceName", "")
        start_date = _parse_apple_date(elem.get("startDate"))
        end_date = _parse_apple_date(elem.get("endDate"))

        subject = f"{rtype_label}: {value}{(' ' + unit) if unit else ''}" if value else rtype_label
        body_text = subject[:MAX_BODY_LEN]

        dedup_seed = f"apple-health|record|{rtype}|{start_date}|{end_date}|{value}|{unit}|{source_name}"
        raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

        row = AdapterRow(
            schema_type="Observation",
            rfc822_message_id=f"apple-health:rec:{raw_hash[:16]}",
            subject=subject,
            sender_address=f"apple-health:{source_name}",
            sender_name=source_name,
            direction="self",
            date_sent=start_date,
            date_received=end_date,
            body_text=body_text,
            body_text_source="apple-health-xml",
            is_bulk=1,
            bulk_signal="apple-health-record",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
        )

        report.rows_yielded += 1
        message_id = self._insert_row(conn, row, source_file_id)
        if message_id is None:
            report.rows_skipped += 1
            return
        report.rows_inserted += 1

        self._link_message_thread(conn, message_id, metrics_thread_id)

        for me in elem.findall("MetadataEntry"):
            k = me.get("key")
            v = me.get("value")
            if k:
                conn.execute(
                    "INSERT INTO record_metadata (message_id, key, value) VALUES (?, ?, ?)",
                    (message_id, k, v),
                )

        hr_list = elem.find("HeartRateVariabilityMetadataList")
        if hr_list is not None:
            for ib in hr_list.findall("InstantaneousBeatsPerMinute"):
                ib_bpm = ib.get("bpm")
                ib_time = _parse_apple_date(ib.get("time"))
                if ib_bpm and ib_time:
                    try:
                        bpm_int = int(float(ib_bpm))
                    except ValueError:
                        continue
                    conn.execute(
                        "INSERT INTO hr_samples (parent_message_id, ts, bpm) VALUES (?, ?, ?)",
                        (message_id, ib_time, bpm_int),
                    )

    def _handle_workout(
        self,
        conn: sqlite3.Connection,
        elem: ET.Element,
        source_file_id: int,
        zf: zipfile.ZipFile,
        report: IngestReport,
    ) -> set[int]:
        touched: set[int] = set()

        activity = elem.get("workoutActivityType", "")
        activity_label = _strip_hk_prefix(activity)
        duration = elem.get("duration")
        duration_unit = elem.get("durationUnit", "")
        total_distance = elem.get("totalDistance")
        distance_unit = elem.get("totalDistanceUnit", "")
        energy = elem.get("totalEnergyBurned")
        energy_unit = elem.get("totalEnergyBurnedUnit", "")
        source_name = elem.get("sourceName", "")
        start_date = _parse_apple_date(elem.get("startDate"))
        end_date = _parse_apple_date(elem.get("endDate"))

        parts = [f"Workout: {activity_label}"]
        if duration:
            parts.append(f"duration {duration} {duration_unit}".strip())
        if total_distance:
            parts.append(f"distance {total_distance} {distance_unit}".strip())
        if energy:
            parts.append(f"energy {energy} {energy_unit}".strip())
        body_text = " | ".join(parts)[:MAX_BODY_LEN]
        subject = parts[0]

        dedup_seed = f"apple-health|workout|{activity}|{start_date}|{end_date}|{source_name}"
        raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

        row = AdapterRow(
            schema_type="ExerciseAction",
            rfc822_message_id=f"apple-health:wkt:{raw_hash[:16]}",
            subject=subject,
            sender_address=f"apple-health:{source_name}",
            sender_name=source_name,
            direction="self",
            date_sent=start_date,
            date_received=end_date,
            body_text=body_text,
            body_text_source="apple-health-xml",
            is_bulk=1,
            bulk_signal="apple-health-workout",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
        )

        report.rows_yielded += 1
        message_id = self._insert_row(conn, row, source_file_id)
        if message_id is None:
            report.rows_skipped += 1
            return touched
        report.rows_inserted += 1

        thread_key = f"apple-health:workout:{raw_hash[:16]}"
        thread_id, created = self._upsert_thread(conn, thread_key)
        self._link_message_thread(conn, message_id, thread_id)
        if created:
            report.threads_created += 1
        touched.add(thread_id)

        conn.execute(
            "UPDATE threads SET date_first=?, date_last=? WHERE id=?",
            (start_date, end_date, thread_id),
        )

        for we in elem.findall("WorkoutEvent"):
            ev_type = we.get("type")
            ev_date = _parse_apple_date(we.get("date"))
            ev_dur = we.get("duration")
            ev_dur_unit = we.get("durationUnit", "")
            ev_dur_seconds: float | None = None
            if ev_dur:
                try:
                    d = float(ev_dur)
                    if ev_dur_unit == "min":
                        ev_dur_seconds = d * 60.0
                    elif ev_dur_unit == "hr":
                        ev_dur_seconds = d * 3600.0
                    else:
                        ev_dur_seconds = d
                except ValueError:
                    pass
            conn.execute(
                "INSERT INTO workout_events (workout_message_id, event_type, date, duration_seconds) VALUES (?, ?, ?, ?)",
                (message_id, ev_type, ev_date, ev_dur_seconds),
            )

        for ws in elem.findall("WorkoutStatistics"):
            st_type = ws.get("type")
            if not st_type:
                continue
            conn.execute(
                """INSERT INTO workout_statistics
                      (workout_message_id, stat_type, value_min, value_avg, value_max, value_sum,
                       unit, date_start, date_end)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    st_type,
                    _safe_float(ws.get("minimum")),
                    _safe_float(ws.get("average")),
                    _safe_float(ws.get("maximum")),
                    _safe_float(ws.get("sum")),
                    ws.get("unit"),
                    _parse_apple_date(ws.get("startDate")),
                    _parse_apple_date(ws.get("endDate")),
                ),
            )

        for wr in elem.findall("WorkoutRoute"):
            fr = wr.find("FileReference")
            if fr is None:
                continue
            gpx_rel = fr.get("path", "")
            if not gpx_rel:
                continue
            zip_internal = "apple_health_export" + gpx_rel
            try:
                with zf.open(zip_internal) as gf:
                    self._ingest_gpx(conn, gf, message_id)
            except KeyError:
                pass

        return touched

    def _ingest_gpx(
        self,
        conn: sqlite3.Connection,
        file_obj: IO[bytes],
        parent_message_id: int,
    ) -> None:
        point_idx = 0
        for _ev, elem in ET.iterparse(file_obj, events=("end",)):
            tag_local = elem.tag.split("}")[-1]
            if tag_local != "trkpt":
                elem.clear()
                continue
            try:
                lat = float(elem.get("lat", ""))
                lon = float(elem.get("lon", ""))
            except (TypeError, ValueError):
                elem.clear()
                continue

            ele_v: float | None = None
            ts_v: str | None = None
            speed: float | None = None
            course: float | None = None
            h_acc: float | None = None
            v_acc: float | None = None

            for child in list(elem):
                local = child.tag.split("}")[-1]
                txt = (child.text or "").strip() if child.text else ""
                if local == "ele" and txt:
                    ele_v = _safe_float(txt)
                elif local == "time" and txt:
                    ts_v = txt
                elif local == "extensions":
                    for sub in child.iter():
                        sub_local = sub.tag.split("}")[-1]
                        sub_txt = (sub.text or "").strip() if sub.text else ""
                        if not sub_txt:
                            continue
                        if sub_local == "speed":
                            speed = _safe_float(sub_txt)
                        elif sub_local == "course":
                            course = _safe_float(sub_txt)
                        elif sub_local == "hAcc":
                            h_acc = _safe_float(sub_txt)
                        elif sub_local == "vAcc":
                            v_acc = _safe_float(sub_txt)

            conn.execute(
                """INSERT INTO geo_traces
                      (parent_message_id, source_kind, point_idx, ts, lat, lon,
                       elevation_m, speed_mps, course, horizontal_accuracy_m,
                       vertical_accuracy_m, extra_json)
                   VALUES (?, 'apple-health-gpx', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    parent_message_id, point_idx, ts_v, lat, lon, ele_v,
                    speed, course, h_acc, v_acc, None,
                ),
            )
            point_idx += 1
            elem.clear()

    def _handle_clinical(
        self,
        conn: sqlite3.Connection,
        elem: ET.Element,
        source_file_id: int,
        clinical_thread_id: int,
        report: IngestReport,
    ) -> None:
        rtype = elem.get("type", "")
        identifier = elem.get("identifier", "")
        source_name = elem.get("sourceName", "")
        received_date = _parse_apple_date(elem.get("receivedDate"))
        fhir_resource_type = elem.get("fhirResourceType", "")

        subject = f"Clinical: {fhir_resource_type or rtype}"
        body_text = f"{rtype} | {identifier} | source={source_name}"[:MAX_BODY_LEN]

        dedup_seed = f"apple-health|clinical|{rtype}|{identifier}|{received_date}"
        raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

        row = AdapterRow(
            schema_type="MedicalRecord",
            rfc822_message_id=f"apple-health:clin:{raw_hash[:16]}",
            subject=subject,
            sender_address=f"apple-health:{source_name}",
            sender_name=source_name,
            direction="inbound",
            date_sent=received_date,
            body_text=body_text,
            body_text_source="apple-health-xml",
            is_bulk=1,
            bulk_signal="apple-health-clinical",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
        )

        report.rows_yielded += 1
        message_id = self._insert_row(conn, row, source_file_id)
        if message_id is None:
            report.rows_skipped += 1
            return
        report.rows_inserted += 1

        self._link_message_thread(conn, message_id, clinical_thread_id)
