"""iCal format parser — yields CalendarEvent records from .ics files.

Handles zip, directory, or bare .ics file sources.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from phdb.records import CalendarEvent, Provenance

_SUPPORTED_EXTS = {".ics", ".ical"}


def _unfold_lines(text: str) -> list[str]:
    """iCal RFC 5545: lines beginning with whitespace are continuations."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line and (line[0] == " " or line[0] == "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape_ical(s: str | None) -> str | None:
    if s is None:
        return None
    return (
        s.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _parse_ical_datetime(value: str, params: dict[str, str]) -> str | None:
    if not value:
        return None
    is_date_only = params.get("VALUE") == "DATE"
    s = value.strip()
    fmts: list[str] = []
    if is_date_only or len(s) == 8:
        fmts.append("%Y%m%d")
    if "T" in s:
        fmts.append("%Y%m%dT%H%M%SZ" if s.endswith("Z") else "%Y%m%dT%H%M%S")
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.date().isoformat() if fmt == "%Y%m%d" else dt.isoformat()
        except ValueError:
            continue
    return None


def _parse_ical_text(text: str) -> tuple[dict[str, str], list[dict[str, str | None]]]:
    lines = _unfold_lines(text)
    cal_meta: dict[str, str] = {}
    events: list[dict[str, str | None]] = []
    in_cal = False
    in_event: dict[str, str | None] | None = None

    for line in lines:
        if not line.strip():
            continue
        if line == "BEGIN:VCALENDAR":
            in_cal = True
            continue
        if line == "END:VCALENDAR":
            in_cal = False
            continue
        if not in_cal:
            continue
        if line == "BEGIN:VEVENT":
            in_event = {}
            continue
        if line == "END:VEVENT":
            if in_event:
                events.append(in_event)
            in_event = None
            continue
        if line.startswith("BEGIN:") or line.startswith("END:"):
            continue

        m = re.match(r"^([A-Z0-9-]+)([^:]*):(.*)$", line)
        if not m:
            continue
        name, params_str, value = m.group(1), m.group(2), m.group(3)
        params: dict[str, str] = {}
        if params_str:
            for p in params_str.lstrip(";").split(";"):
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k.upper()] = v

        if in_event is not None:
            if name in ("DTSTART", "DTEND", "DTSTAMP", "CREATED", "LAST-MODIFIED"):
                in_event[name] = _parse_ical_datetime(value, params)
            elif name == "SUMMARY":
                in_event["SUMMARY"] = _unescape_ical(value)
            elif name == "DESCRIPTION":
                in_event["DESCRIPTION"] = _unescape_ical(value)
            elif name == "LOCATION":
                in_event["LOCATION"] = _unescape_ical(value)
            elif name == "UID":
                in_event["UID"] = value
        else:
            if name in ("X-WR-CALNAME", "X-WR-TIMEZONE", "X-WR-CALDESC", "PRODID"):
                cal_meta[name] = value.strip()

    return cal_meta, events


def _yield_ics_files(source_path: Path) -> Iterator[tuple[str, str]]:
    if source_path.is_file() and source_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if Path(name).suffix.lower() in _SUPPORTED_EXTS:
                    yield name, zf.read(name).decode("utf-8", errors="replace")
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("*")):
            if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTS:
                yield str(p.relative_to(source_path)), p.read_text(encoding="utf-8", errors="replace")
    elif source_path.is_file() and source_path.suffix.lower() in _SUPPORTED_EXTS:
        yield source_path.name, source_path.read_text(encoding="utf-8", errors="replace")


def parse(source_path: Path) -> Iterator[CalendarEvent]:
    """Parse iCal files, yielding CalendarEvent records."""
    source_str = str(source_path)

    for source_label, ics_text in _yield_ics_files(source_path):
        cal_meta, events = _parse_ical_text(ics_text)
        calendar_name = cal_meta.get("X-WR-CALNAME") or Path(source_label).stem.split("@")[0]

        for evt_idx, evt in enumerate(events):
            date_start = evt.get("DTSTART")
            uid = evt.get("UID") or f"calendar:{calendar_name}:idx{evt_idx}"

            dedup_seed = f"calendar|{calendar_name}|{uid}|{date_start}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            is_all_day = bool(date_start and len(date_start) == 10)

            yield CalendarEvent(
                provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                uid=uid,
                date_start=date_start or "",
                calendar_name=calendar_name,
                is_all_day=is_all_day,
                summary=evt.get("SUMMARY"),
                description=evt.get("DESCRIPTION"),
                location=evt.get("LOCATION"),
                date_end=evt.get("DTEND"),
            )
