"""Google Fit adapter — ingests derived metric JSONs from Google Takeout.

Source: a Takeout zip or directory with Fit/All Data/*.json files.
Activity segments -> ExerciseAction; everything else -> Observation.
Per-metric threads. All is_bulk=1.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_fit")

_ACTIVITY_TYPES: dict[int, str] = {
    0: "Sitting", 1: "Cycling", 2: "On foot", 3: "Still", 4: "Unknown",
    5: "Tilting", 7: "Walking", 8: "Running", 9: "Aerobics",
    10: "Badminton", 11: "Baseball", 12: "Basketball", 78: "Sleep",
}
_EXERCISE_DATATYPES = {"com.google.activity.segment", "com.google.activity.exercise"}


def _short_metric(datatype_name: str) -> str:
    if datatype_name.startswith("com.google."):
        return datatype_name[11:]
    return datatype_name


def _fmt_value(fit_value: list[dict[str, object]] | None) -> object:
    if not fit_value:
        return None
    out: list[object] = []
    for item in fit_value:
        v = item.get("value", {})
        if not isinstance(v, dict):
            continue
        if "fpVal" in v:
            out.append(v["fpVal"])
        elif "intVal" in v:
            out.append(v["intVal"])
        elif "stringVal" in v:
            out.append(v["stringVal"])
    return out[0] if len(out) == 1 else (out or None)


def _yield_fit_files(source_path: Path) -> Iterator[tuple[str, bytes]]:
    if source_path.is_file() and source_path.suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if name.startswith("Takeout/Fit/All Data/") and name.endswith(".json"):
                    yield name, zf.read(name)
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("*.json")):
            yield str(p.relative_to(source_path)), p.read_bytes()


class GoogleFitAdapter(Adapter):
    """Ingest Google Fit derived metric JSONs."""

    name = "google_fit"
    source_kind = "google-fit"
    file_kind = "json"
    schema_type = "Observation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 1000

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for fi, (_relpath, json_bytes) in enumerate(_yield_fit_files(source_path)):
            try:
                data = json.loads(json_bytes)
            except json.JSONDecodeError:
                continue

            for pi, dp in enumerate(data.get("Data Points", [])):
                datatype = dp.get("dataTypeName", "unknown")
                metric = _short_metric(datatype)
                value = _fmt_value(dp.get("fitValue"))

                start_ns = dp.get("startTimeNanos")
                end_ns = dp.get("endTimeNanos")
                if start_ns is None:
                    continue

                try:
                    ts = datetime.fromtimestamp(int(start_ns) / 1e9, tz=UTC).isoformat()
                    ts_end = (
                        datetime.fromtimestamp(int(end_ns) / 1e9, tz=UTC).isoformat()
                        if end_ns else None
                    )
                except (ValueError, OSError):
                    continue

                schema_t = "ExerciseAction" if datatype in _EXERCISE_DATATYPES else "Observation"

                if datatype == "com.google.activity.segment" and isinstance(value, int):
                    value_str = _ACTIVITY_TYPES.get(value, f"activity_type_{value}")
                else:
                    value_str = str(value)

                subject = f"{metric}: {value_str}"[:200]
                body = f"{metric} = {value_str}\nstart={ts} end={ts_end}"[:1000]

                dedup_seed = f"google-fit|{metric}|{start_ns}|{value_str}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type=schema_t,
                    rfc822_message_id=f"google-fit:{raw_hash}",
                    subject=subject,
                    sender_address="google-fit:self",
                    sender_name=metric,
                    direction="self",
                    date_sent=ts,
                    date_received=ts_end,
                    body_text=body,
                    body_text_source="google-fit-json",
                    is_bulk=1,
                    bulk_signal="google-fit-datapoint",
                    source_byte_offset=fi,
                    source_byte_length=pi,
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    thread_key=f"google-fit:{metric}",
                )

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        return True, "google-fit-datapoint"
