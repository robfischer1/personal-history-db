"""Apple Health Export XML parser — yields typed parsed records.

Source: Health_Export.zip containing apple_health_export/export.xml + GPX routes.
Three element types:
  <Record>         -> ParsedRecord (observation/measurement)
  <Workout>        -> ParsedWorkout (exercise session + events/stats/routes)
  <ClinicalRecord> -> ParsedClinical (FHIR clinical data)

Pure parser: no DB, no identity, no AdapterRow.  Streaming XML via iterparse
with periodic root.clear() for memory-flat processing of multi-GB exports.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT_CLEAR_EVERY = 10000

HK_PREFIXES = (
    "HKQuantityTypeIdentifier",
    "HKCategoryTypeIdentifier",
    "HKWorkoutActivityType",
    "HKDataType",
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def strip_hk_prefix(s: str) -> str:
    """Remove Apple HealthKit type-identifier prefixes."""
    if not s:
        return s
    for p in HK_PREFIXES:
        if s.startswith(p):
            return s[len(p):]
    return s


def parse_apple_date(s: str | None) -> str | None:
    """Parse Apple Health date strings to UTC ISO-8601."""
    if not s:
        return None
    s = s.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            dt = datetime.strptime(
                s.replace("Z", "+0000") if fmt.endswith("Z") else s, fmt,
            )
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            continue
    return s


def safe_float(v: str | None) -> float | None:
    """Convert string to float, returning None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Parsed-record dataclasses (format-layer intermediates)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetadataEntry:
    """One <MetadataEntry> child of a <Record>."""
    key: str
    value: str | None


@dataclass(frozen=True)
class HRSample:
    """One <InstantaneousBeatsPerMinute> from a HeartRateVariabilityMetadataList."""
    ts: str
    bpm: int


@dataclass(frozen=True)
class ParsedRecord:
    """Parsed <Record> element — a single observation/measurement."""
    record_type: str
    record_type_label: str
    unit: str
    value: str
    source_name: str
    start_date: str | None
    end_date: str | None
    subject: str
    body_text: str
    raw_hash: str
    metadata: tuple[MetadataEntry, ...] = ()
    hr_samples: tuple[HRSample, ...] = ()


@dataclass(frozen=True)
class WorkoutEvent:
    """One <WorkoutEvent> child of a <Workout>."""
    event_type: str | None
    date: str | None
    duration_seconds: float | None


@dataclass(frozen=True)
class WorkoutStatistic:
    """One <WorkoutStatistics> child of a <Workout>."""
    stat_type: str
    value_min: float | None
    value_avg: float | None
    value_max: float | None
    value_sum: float | None
    unit: str | None
    date_start: str | None
    date_end: str | None


@dataclass(frozen=True)
class GpxPoint:
    """One <trkpt> from a GPX route file."""
    lat: float
    lon: float
    ts: str | None = None
    elevation_m: float | None = None
    speed_mps: float | None = None
    course: float | None = None
    horizontal_accuracy_m: float | None = None
    vertical_accuracy_m: float | None = None


@dataclass(frozen=True)
class ParsedWorkout:
    """Parsed <Workout> element — an exercise session with sidecar data."""
    activity_type: str
    activity_label: str
    duration: str | None
    duration_unit: str
    total_distance: str | None
    distance_unit: str
    energy_burned: str | None
    energy_unit: str
    source_name: str
    start_date: str | None
    end_date: str | None
    subject: str
    body_text: str
    raw_hash: str
    events: tuple[WorkoutEvent, ...] = ()
    statistics: tuple[WorkoutStatistic, ...] = ()
    gpx_points: tuple[GpxPoint, ...] = ()


@dataclass(frozen=True)
class ParsedClinical:
    """Parsed <ClinicalRecord> element — a FHIR clinical record."""
    record_type: str
    identifier: str
    source_name: str
    received_date: str | None
    fhir_resource_type: str
    subject: str
    body_text: str
    raw_hash: str


# Union of all parsed types
ParsedElement = ParsedRecord | ParsedWorkout | ParsedClinical


# ---------------------------------------------------------------------------
# Element parsers
# ---------------------------------------------------------------------------

MAX_BODY_LEN = 2000


def _parse_record_elem(elem: ET.Element) -> ParsedRecord:
    """Parse a <Record> XML element into a ParsedRecord."""
    rtype = elem.get("type", "")
    rtype_label = strip_hk_prefix(rtype)
    unit = elem.get("unit", "")
    value = elem.get("value", "")
    source_name = elem.get("sourceName", "")
    start_date = parse_apple_date(elem.get("startDate"))
    end_date = parse_apple_date(elem.get("endDate"))

    subject = (
        f"{rtype_label}: {value}{(' ' + unit) if unit else ''}"
        if value
        else rtype_label
    )
    body_text = subject[:MAX_BODY_LEN]

    dedup_seed = (
        f"apple-health|record|{rtype}|{start_date}|{end_date}"
        f"|{value}|{unit}|{source_name}"
    )
    raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

    # Metadata entries
    metadata: list[MetadataEntry] = []
    for me in elem.findall("MetadataEntry"):
        k = me.get("key")
        if k:
            metadata.append(MetadataEntry(key=k, value=me.get("value")))

    # HR variability samples
    hr_samples: list[HRSample] = []
    hr_list = elem.find("HeartRateVariabilityMetadataList")
    if hr_list is not None:
        for ib in hr_list.findall("InstantaneousBeatsPerMinute"):
            ib_bpm = ib.get("bpm")
            ib_time = parse_apple_date(ib.get("time"))
            if ib_bpm and ib_time:
                try:
                    bpm_int = int(float(ib_bpm))
                except ValueError:
                    continue
                hr_samples.append(HRSample(ts=ib_time, bpm=bpm_int))

    return ParsedRecord(
        record_type=rtype,
        record_type_label=rtype_label,
        unit=unit,
        value=value,
        source_name=source_name,
        start_date=start_date,
        end_date=end_date,
        subject=subject,
        body_text=body_text,
        raw_hash=raw_hash,
        metadata=tuple(metadata),
        hr_samples=tuple(hr_samples),
    )


def _parse_workout_event(we: ET.Element) -> WorkoutEvent:
    """Parse a <WorkoutEvent> child element."""
    ev_type = we.get("type")
    ev_date = parse_apple_date(we.get("date"))
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
    return WorkoutEvent(
        event_type=ev_type,
        date=ev_date,
        duration_seconds=ev_dur_seconds,
    )


def _parse_workout_statistic(ws: ET.Element) -> WorkoutStatistic | None:
    """Parse a <WorkoutStatistics> child element. Returns None if no type."""
    st_type = ws.get("type")
    if not st_type:
        return None
    return WorkoutStatistic(
        stat_type=st_type,
        value_min=safe_float(ws.get("minimum")),
        value_avg=safe_float(ws.get("average")),
        value_max=safe_float(ws.get("maximum")),
        value_sum=safe_float(ws.get("sum")),
        unit=ws.get("unit"),
        date_start=parse_apple_date(ws.get("startDate")),
        date_end=parse_apple_date(ws.get("endDate")),
    )


def parse_gpx(file_obj: IO[bytes]) -> list[GpxPoint]:
    """Parse a GPX file, returning a list of GpxPoint track-points."""
    points: list[GpxPoint] = []
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
                ele_v = safe_float(txt)
            elif local == "time" and txt:
                ts_v = txt
            elif local == "extensions":
                for sub in child.iter():
                    sub_local = sub.tag.split("}")[-1]
                    sub_txt = (sub.text or "").strip() if sub.text else ""
                    if not sub_txt:
                        continue
                    if sub_local == "speed":
                        speed = safe_float(sub_txt)
                    elif sub_local == "course":
                        course = safe_float(sub_txt)
                    elif sub_local == "hAcc":
                        h_acc = safe_float(sub_txt)
                    elif sub_local == "vAcc":
                        v_acc = safe_float(sub_txt)

        points.append(GpxPoint(
            lat=lat,
            lon=lon,
            ts=ts_v,
            elevation_m=ele_v,
            speed_mps=speed,
            course=course,
            horizontal_accuracy_m=h_acc,
            vertical_accuracy_m=v_acc,
        ))
        elem.clear()
    return points


def _parse_workout_elem(
    elem: ET.Element,
    zf: zipfile.ZipFile,
) -> ParsedWorkout:
    """Parse a <Workout> XML element into a ParsedWorkout."""
    activity = elem.get("workoutActivityType", "")
    activity_label = strip_hk_prefix(activity)
    duration = elem.get("duration")
    duration_unit = elem.get("durationUnit", "")
    total_distance = elem.get("totalDistance")
    distance_unit = elem.get("totalDistanceUnit", "")
    energy = elem.get("totalEnergyBurned")
    energy_unit = elem.get("totalEnergyBurnedUnit", "")
    source_name = elem.get("sourceName", "")
    start_date = parse_apple_date(elem.get("startDate"))
    end_date = parse_apple_date(elem.get("endDate"))

    parts = [f"Workout: {activity_label}"]
    if duration:
        parts.append(f"duration {duration} {duration_unit}".strip())
    if total_distance:
        parts.append(f"distance {total_distance} {distance_unit}".strip())
    if energy:
        parts.append(f"energy {energy} {energy_unit}".strip())
    body_text = " | ".join(parts)[:MAX_BODY_LEN]
    subject = parts[0]

    dedup_seed = (
        f"apple-health|workout|{activity}|{start_date}|{end_date}|{source_name}"
    )
    raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

    # Workout events
    events: list[WorkoutEvent] = []
    for we in elem.findall("WorkoutEvent"):
        events.append(_parse_workout_event(we))

    # Workout statistics
    statistics: list[WorkoutStatistic] = []
    for ws in elem.findall("WorkoutStatistics"):
        stat = _parse_workout_statistic(ws)
        if stat is not None:
            statistics.append(stat)

    # GPX routes
    gpx_points: list[GpxPoint] = []
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
                gpx_points.extend(parse_gpx(gf))
        except KeyError:
            pass

    return ParsedWorkout(
        activity_type=activity,
        activity_label=activity_label,
        duration=duration,
        duration_unit=duration_unit,
        total_distance=total_distance,
        distance_unit=distance_unit,
        energy_burned=energy,
        energy_unit=energy_unit,
        source_name=source_name,
        start_date=start_date,
        end_date=end_date,
        subject=subject,
        body_text=body_text,
        raw_hash=raw_hash,
        events=tuple(events),
        statistics=tuple(statistics),
        gpx_points=tuple(gpx_points),
    )


def _parse_clinical_elem(elem: ET.Element) -> ParsedClinical:
    """Parse a <ClinicalRecord> XML element into a ParsedClinical."""
    rtype = elem.get("type", "")
    identifier = elem.get("identifier", "")
    source_name = elem.get("sourceName", "")
    received_date = parse_apple_date(elem.get("receivedDate"))
    fhir_resource_type = elem.get("fhirResourceType", "")

    subject = f"Clinical: {fhir_resource_type or rtype}"
    body_text = f"{rtype} | {identifier} | source={source_name}"[:MAX_BODY_LEN]

    dedup_seed = f"apple-health|clinical|{rtype}|{identifier}|{received_date}"
    raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

    return ParsedClinical(
        record_type=rtype,
        identifier=identifier,
        source_name=source_name,
        received_date=received_date,
        fhir_resource_type=fhir_resource_type,
        subject=subject,
        body_text=body_text,
        raw_hash=raw_hash,
    )


# ---------------------------------------------------------------------------
# Top-level streaming parser
# ---------------------------------------------------------------------------

def parse(source_path: Path) -> Iterator[ParsedElement]:
    """Stream Apple Health export XML, yielding parsed records.

    Opens the zip, streams export.xml via iterparse, and yields one
    ParsedRecord / ParsedWorkout / ParsedClinical per relevant element.
    Memory-flat: periodic root.clear() prevents DOM accumulation.
    """
    with zipfile.ZipFile(source_path) as zf, \
         zf.open("apple_health_export/export.xml") as f:
        context = ET.iterparse(f, events=("start", "end"))
        _event, root = next(context)
        processed = 0

        for event, elem in context:
            if event != "end":
                continue

            tag = elem.tag
            if tag == "Record":
                yield _parse_record_elem(elem)
            elif tag == "Workout":
                yield _parse_workout_elem(elem, zf)
            elif tag == "ClinicalRecord":
                yield _parse_clinical_elem(elem)
            else:
                continue

            elem.clear()
            processed += 1

            if processed % ROOT_CLEAR_EVERY == 0:
                root.clear()
