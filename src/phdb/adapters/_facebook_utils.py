"""Shared utilities for Facebook adapters."""

from __future__ import annotations

import re
from datetime import datetime

FB_TS_RE = re.compile(
    r"^([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+(?:am|pm))$"
)


def parse_fb_timestamp(text: str | None) -> str | None:
    if not text:
        return None
    s = text.strip()
    if not FB_TS_RE.match(s):
        return None
    try:
        return datetime.strptime(s, "%b %d, %Y %I:%M:%S %p").isoformat()
    except ValueError:
        return None
