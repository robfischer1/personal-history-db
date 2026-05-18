"""Google Fit JSON format parser — yields HealthObservation records.

Parses derived metric JSONs from Google Takeout (zip or directory).
Activity segments -> observation_type with category "exercise";
everything else -> observation_type with category "metric".
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.records import HealthObservation, Provenance

_ACTIVITY_TYPES: dict[int, str] = {
    0: "Sitting", 1: "Cycling", 2: "On foot", 3: "Still", 4: "Unknown",
    5: "Tilting", 7: "Walking", 8: "Running", 9: "Aerobics",
    10: "Badminton", 11: "Baseball", 12: "Basketball", 78: "Sleep",
}
_EXERCISE_DATATYPES = {"com.google.activity.segment", "com.google.activity.exercise"}


def _short_metric(datatype_name: str) -> str:
    """Strip the com.google. prefix from a datatype name."""
    if datatype_name.startswith("com.google."):
        return datatype_name[11:]
    return datatype_name


def _fmt_value(fit_value: list[dict[str, object]] | None) -> object:
    """Extract the scalar value from a fitValue array."""
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
    """Yield (relative_path, json_bytes) for each Fit JSON in the source."""
    if source_path.is_file() and source_path.suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if name.startswith("Takeout/Fit/All Data/") and name.endswith(".json"):
                    yield name, zf.read(name)
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("*.json")):
            yield str(p.relative_to(source_path)), p.read_bytes()


def parse(source_path: Path) -> Iterator[HealthObservation]:
    """Parse Google Fit JSON files, yielding HealthObservation records."""
    source_str = str(source_path)

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

            category = "exercise" if datatype in _EXERCISE_DATATYPES else "metric"

            # Format value string for hash and metadata
            if datatype == "com.google.activity.segment" and isinstance(value, int):
                value_str = _ACTIVITY_TYPES.get(value, f"activity_type_{value}")
            else:
                value_str = str(value)

            dedup_seed = f"google-fit|{metric}|{start_ns}|{value_str}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            # Numeric value for the record field
            numeric_value: float | None = None
            if isinstance(value, (int, float)):
                numeric_value = float(value)

            # Build metadata tuple
            meta: list[tuple[str, str]] = [("category", category)]
            if category == "exercise" and datatype == "com.google.activity.segment":
                meta.append(("activity_name", value_str))
            if value_str and numeric_value is None:
                meta.append(("value_str", value_str))

            yield HealthObservation(
                provenance=Provenance(
                    source_path=source_str,
                    raw_hash=raw_hash,
                    source_byte_offset=fi,
                    source_byte_length=pi,
                ),
                observation_type=metric,
                date_start=ts,
                value=numeric_value,
                date_end=ts_end,
                metadata=tuple(meta),
            )
