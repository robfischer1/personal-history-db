"""Shared URL utilities — normalization, junk detection, domain extraction.

Moved from formats/raindrop.py so any adapter producing URL entities can reuse them.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


# ---------------------------------------------------------------------------
# Tracking params stripped during normalization
# ---------------------------------------------------------------------------

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_brand", "utm_social", "utm_social-type",
    "fbclid", "gclid", "msclkid", "dclid",
    "mc_cid", "mc_eid",
    "_ga", "_gl", "yclid", "ref_src", "ref_url",
    "gad_source", "gad_campaignid", "gbraid", "wbraid",
    "rlz", "oq", "aqs", "sourceid", "ie", "client", "gs_lcrp", "sxsrf",
    "ved", "uact", "ei", "iflsig", "esrc", "sa", "vet", "biw", "bih",
    "psig", "gs_lp", "gs_ssp", "udm",
}


# ---------------------------------------------------------------------------
# Junk URL patterns — matched URLs get excluded=1
# ---------------------------------------------------------------------------

JUNK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^https?://(www\.)?gmail\.com/?$"), "junk:gmail-root"),
    (re.compile(r"^https?://mail\.google\.com/(mail/?(u/\d+/?)?(\?.*)?(#inbox/?)?)?$"), "junk:gmail-inbox"),
    (re.compile(r"^https?://(www\.)?amazon\.com/?$"), "junk:amazon-root"),
    (re.compile(r"^https?://(www\.)?google\.com/?(\?.*)?$"), "junk:google-root"),
    (re.compile(r"^https?://(www\.)?facebook\.com/?$"), "junk:facebook-root"),
    (re.compile(r"^https?://(www\.)?twitter\.com/?$"), "junk:twitter-root"),
    (re.compile(r"^https?://(www\.)?youtube\.com/?$"), "junk:youtube-root"),
    (re.compile(r"^https?://(www\.)?reddit\.com/?$"), "junk:reddit-root"),
    (re.compile(r"^https?://(www\.)?old\.reddit\.com/?$"), "junk:reddit-root"),
    (re.compile(r"^https?://calendar\.google\.com/calendar/u/\d+/r/?(week|month|day|agenda)?/?$"), "junk:google-calendar-landing"),
    (re.compile(r"^https?://contacts\.google\.com/u/\d+/?$"), "junk:google-contacts-landing"),
    (re.compile(r"^https?://chrome\.google\.com/webstore"), "junk:chrome-webstore"),
    (re.compile(r"^https?://redirect\.hp\.com/"), "junk:hp-factory-redirect"),
    (re.compile(r"^https?://go\.microsoft\.com/fwlink/"), "junk:microsoft-fwlink"),
    (re.compile(r"^chrome://"), "junk:chrome-internal"),
    (re.compile(r"^javascript:"), "junk:javascript-bookmarklet"),
    (re.compile(r"^about:"), "junk:browser-internal"),
    (re.compile(r"^file://"), "junk:local-file"),
    (re.compile(r"^https?://localhost(:\d+)?/?"), "junk:localhost"),
    (re.compile(r"^https?://127\.0\.0\.1(:\d+)?/?"), "junk:localhost"),
]

SKIP_URL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^https?://(www\.)?google\.com/search\?"), "skip:google-search-result-redundant-with-SearchAction"),
    (re.compile(r"^https?://(www\.)?google\.com/url\?"), "skip:google-redirect-redundant-with-SearchAction"),
]


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

def normalize_url(raw: str) -> str:
    """Conservative normalization for cross-instrument dedup."""
    if not raw:
        return ""
    raw = raw.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    scheme = (parts.scheme or "").lower()
    netloc = parts.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    if scheme == "http":
        scheme = "https"
    path = (parts.path or "").rstrip("/")
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in TRACKING_PARAMS]
        query = urlencode(kept, doseq=True)
    else:
        query = ""
    return urlunsplit((scheme, netloc, path, query, ""))


def is_junk(url: str) -> str | None:
    """Return junk-reason if URL is junk, else None."""
    if not url:
        return "junk:empty-url"
    for pat, reason in JUNK_PATTERNS:
        if pat.match(url):
            return reason
    return None


def should_skip(url: str) -> str | None:
    """Return skip-reason if URL should not enter the bookmarks table at all."""
    if not url:
        return None
    for pat, reason in SKIP_URL_PATTERNS:
        if pat.match(url):
            return reason
    return None


def extract_domain(normalized_url: str) -> str | None:
    """Extract the domain (netloc) from a normalized URL."""
    if not normalized_url:
        return None
    try:
        parts = urlsplit(normalized_url)
        netloc = parts.netloc
        return netloc if netloc else None
    except ValueError:
        return None
