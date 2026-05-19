"""Legacy IM chat logs parser — yields ChatMessage records grouped by session.

Supports three formats:
  1. AIM HTML (.htm) — color-coded SPAN/FONT blocks
  2. Plaintext session (.log/.txt) — Trillian-derived Session Start/Close format
  3. Bracketed-time (.log/.txt) — [HH:MM] Sender: msg format

Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from phdb.records import ChatMessage, Provenance

_MAX_BODY_LEN = 50_000
_SUPPORTED_EXTS = {".htm", ".html", ".txt", ".log"}

AIM_MSG_RE = re.compile(
    r'<B><FONT\s+COLOR="(?P<sender_color>#[0-9a-fA-F]+)"[^>]*>'
    r"(?P<sender>[^<]+?)"
    r'<SPAN[^>]*>\s*\((?P<ts>[^)]+)\)</SPAN></B></FONT>'
    r"(?P<rest>.*?)(?:<BR>|</SPAN></BODY>|</SPAN>(?=\s*<HR>|\s*<B>|\s*$))",
    re.IGNORECASE | re.DOTALL,
)

SESSION_OPEN_RE = re.compile(r"^Session Start \(([^)]+)\):\s+(.+)$")
SESSION_CLOSE_RE = re.compile(r"^Session Close \(([^)]+)\):\s+(.+)$")
SESSION_OPEN_BARE_RE = re.compile(r"^Session Start:\s+(.+)$")
MSG_LINE_RE = re.compile(r"^([^:\n]{1,80}?):\s(.*)$")
SYSTEM_LINE_RE = re.compile(r"^\*\*\*\s+(.+)$")

FILENAME_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
FILENAME_MONTHYEAR_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(20\d{2})",
    re.I,
)

AIM_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})\s+(AM|PM)$", re.I)
BRACKETED_TIME_MSG_RE = re.compile(
    r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+([^:\n]{1,80}?):\s(.*)$", re.MULTILINE
)
EVENTS_LOG_RE = re.compile(
    r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M\s+-\s+", re.MULTILINE
)


@dataclass
class ChatSession:
    """One chat session with metadata and messages."""

    protocol: str | None
    my_handle: str | None
    remote_handle: str | None
    start_ts: str | None
    end_ts: str | None
    session_date: str | None
    messages: list[ChatMessage] = field(default_factory=list)


def _html_unescape(s: str) -> str:
    return (
        s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&nbsp;", " ")
    )


def _strip_html_tags(s: str) -> str:
    return _html_unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _strip_msn_color_codes(text: str) -> str:
    return re.sub(r"[\xc2\xa0]?\x03\([\d, ]+\)", "", text)


def _normalize_handle(h: str | None) -> str | None:
    if h is None:
        return None
    h = h.strip()
    if h.lower().startswith("mailto:"):
        h = h[7:]
    return h.lower()


def _parse_session_handle(
    handle_str: str | None,
) -> tuple[str | None, str | None, str | None]:
    if not handle_str:
        return None, None, None
    s = handle_str.strip()
    proto: str | None = None
    my: str | None = None
    remote: str | None = None
    if " - " in s:
        proto_part, rest = s.split(" - ", 1)
        proto = proto_part.strip().lower()
        if ":" in rest:
            my_raw, remote_raw = rest.split(":", 1)
            my = _normalize_handle(my_raw)
            remote = _normalize_handle(remote_raw)
        else:
            remote = _normalize_handle(rest)
    else:
        remote = _normalize_handle(s)
    return proto, my, remote


def _parse_session_timestamp(ts_text: str | None) -> str | None:
    if not ts_text:
        return None
    ts_text = ts_text.strip()
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b  %d %H:%M:%S %Y"):
        try:
            return datetime.strptime(ts_text, fmt).isoformat()
        except ValueError:
            pass
    try:
        return datetime.strptime(ts_text, "%A, %B %d, %Y").date().isoformat()
    except ValueError:
        pass
    return None


def infer_filename_date(file_path: Path) -> datetime | None:
    s = str(file_path)
    m = FILENAME_DATE_RE.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = FILENAME_MONTHYEAR_RE.search(s)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} 1 {m.group(2)}", "%B %d %Y")
        except ValueError:
            pass
    return None


def get_canonical_mtime(file_path: Path) -> datetime | None:
    if file_path.suffix.lower() in (".txt", ".log"):
        sibling = file_path.with_suffix(".ple")
        if sibling.exists():
            return datetime.fromtimestamp(sibling.stat().st_mtime)
    try:
        return datetime.fromtimestamp(file_path.stat().st_mtime)
    except OSError:
        return None


def _combine_date_and_time(
    file_date: datetime | None, ts_local: str
) -> str | None:
    if not file_date:
        return None
    m = AIM_TIME_RE.match(ts_local.strip())
    if not m:
        return file_date.date().isoformat()
    h, mi, s, ampm = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4).upper()
    if ampm == "PM" and h != 12:
        h += 12
    elif ampm == "AM" and h == 12:
        h = 0
    try:
        return datetime(file_date.year, file_date.month, file_date.day, h, mi, s).isoformat()
    except ValueError:
        return file_date.date().isoformat()


def detect_format(file_path: Path, head_bytes: bytes) -> str:
    ext = file_path.suffix.lower()
    head = head_bytes[:2048].decode("utf-8", errors="replace")
    if ext in (".htm", ".html") and (
        "<HTML" in head.upper() or "<BODY" in head.upper() or "<SPAN" in head.upper()
    ):
        return "aim_html"
    if BRACKETED_TIME_MSG_RE.search(head):
        return "bracketed_time"
    head_lines = [line for line in head.split("\n") if line.strip()]
    if head_lines and all(EVENTS_LOG_RE.match(line) for line in head_lines[:10]):
        return "events_log"
    if ext in (".txt", ".log"):
        if "Session Start" in head or "Session Close" in head:
            return "plaintext"
        if re.search(r"^[A-Za-z][\w_]{1,40}: ", head, re.MULTILINE):
            return "plaintext"
    if "Session Start" in head or "Session Close" in head:
        return "plaintext"
    if "<HTML" in head.upper():
        return "aim_html"
    return "unknown"


def _make_message(
    sender_name: str,
    sender_address: str | None,
    date_sent: str | None,
    body_text: str,
    source_path: str,
    file_relpath: str,
    session_index: int,
    msg_index: int,
) -> ChatMessage:
    dedup_seed = (
        f"{file_relpath}|{session_index}|{msg_index}"
        f"|{date_sent}|{sender_address}|{body_text[:100]}"
    )
    raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()
    return ChatMessage(
        provenance=Provenance(
            source_path=source_path,
            raw_hash=raw_hash,
            source_byte_offset=0,
            source_byte_length=msg_index,
        ),
        sender_address=sender_address or sender_name.lower(),
        sender_name=sender_name,
        date_sent=date_sent or "",
        body_text=body_text,
        platform_id=f"chatlog:{raw_hash}",
    )


def _parse_aim_html(
    content: str, file_path: Path, file_date: datetime | None,
    source_str: str, file_relpath: str,
) -> ChatSession | None:
    parts = file_path.parts
    rob_handle: str | None = None
    remote_handle: str | None = None
    if "AIM" in parts:
        idx = parts.index("AIM")
        if idx + 2 < len(parts):
            rob_handle = parts[idx + 1]
            remote_handle = parts[idx + 2]

    msgs_raw = list(AIM_MSG_RE.finditer(content))
    if not msgs_raw:
        return None

    messages: list[ChatMessage] = []
    for i, m in enumerate(msgs_raw):
        sender = m.group("sender").strip()
        ts_local = m.group("ts").strip()
        body = _strip_html_tags(m.group("rest"))
        body = re.sub(r"^[\s:]+", "", body)
        if not body:
            continue
        if len(body) > _MAX_BODY_LEN:
            body = body[:_MAX_BODY_LEN]
        date_sent = _combine_date_and_time(file_date, ts_local)
        messages.append(_make_message(
            sender, _normalize_handle(sender), date_sent, body,
            source_str, file_relpath, 0, i,
        ))

    if not messages:
        return None

    return ChatSession(
        protocol="aim",
        my_handle=_normalize_handle(rob_handle),
        remote_handle=_normalize_handle(remote_handle),
        start_ts=messages[0].date_sent if messages else None,
        end_ts=messages[-1].date_sent if messages else None,
        session_date=file_date.date().isoformat() if file_date else None,
        messages=messages,
    )


def _parse_plaintext_log(
    content: str, file_path: Path, fallback_date: datetime | None,
    source_str: str, file_relpath: str,
) -> list[ChatSession]:
    content = _strip_msn_color_codes(content)
    lines = content.split("\n")

    raw_sessions: list[dict] = []
    current: dict | None = None

    def new_session(
        handle_str: str | None = None, start_ts_text: str | None = None
    ) -> dict:
        proto, my, remote = _parse_session_handle(handle_str)
        return {
            "protocol": proto,
            "my_handle": my,
            "remote_handle": remote,
            "start_ts": _parse_session_timestamp(start_ts_text),
            "raw_msgs": [],
        }

    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            continue
        m_open = SESSION_OPEN_RE.match(line)
        if m_open:
            if current and current["raw_msgs"]:
                raw_sessions.append(current)
            current = new_session(m_open.group(1), m_open.group(2))
            continue
        m_open_bare = SESSION_OPEN_BARE_RE.match(line)
        if m_open_bare:
            if current and current["raw_msgs"]:
                raw_sessions.append(current)
            current = new_session(None, m_open_bare.group(1))
            continue
        m_close = SESSION_CLOSE_RE.match(line)
        if m_close:
            if current is None:
                current = new_session(m_close.group(1))
            current["end_ts"] = _parse_session_timestamp(m_close.group(2))
            if not current.get("remote_handle"):
                _, _, r = _parse_session_handle(m_close.group(1))
                current["remote_handle"] = r
            raw_sessions.append(current)
            current = None
            continue
        m_sys = SYSTEM_LINE_RE.match(line)
        if m_sys:
            if current is None:
                current = new_session()
            continue
        m_msg = MSG_LINE_RE.match(line)
        if m_msg:
            if current is None:
                current = new_session()
            sender = m_msg.group(1).strip()
            body = m_msg.group(2)
            if len(body) > _MAX_BODY_LEN:
                body = body[:_MAX_BODY_LEN]
            current["raw_msgs"].append((sender, body))

    if current and current["raw_msgs"]:
        raw_sessions.append(current)

    result: list[ChatSession] = []
    for sidx, s in enumerate(raw_sessions):
        ts = s.get("start_ts")
        if not ts and fallback_date:
            ts = fallback_date.isoformat()
        session_date = (str(ts) or "")[:10] if ts else None

        messages: list[ChatMessage] = []
        for i, (sender, body) in enumerate(s["raw_msgs"]):
            messages.append(_make_message(
                sender, _normalize_handle(sender),
                str(ts) if ts else None, body,
                source_str, file_relpath, sidx, i,
            ))

        result.append(ChatSession(
            protocol=s.get("protocol"),
            my_handle=s.get("my_handle"),
            remote_handle=s.get("remote_handle"),
            start_ts=str(ts) if ts else None,
            end_ts=s.get("end_ts"),
            session_date=session_date,
            messages=messages,
        ))

    return result


def _parse_bracketed_time_log(
    content: str, file_path: Path, fallback_date: datetime | None,
    source_str: str, file_relpath: str,
) -> list[ChatSession]:
    content = _strip_msn_color_codes(content)
    raw_msgs: list[tuple[str, str, str | None]] = []
    line_re = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+([^:\n]{1,80}?):\s(.*)$")

    for raw in content.split("\n"):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        m = line_re.match(line)
        if m:
            time_part, sender, body = m.group(1), m.group(2).strip(), m.group(3)
            if not body.strip():
                continue
            if len(body) > _MAX_BODY_LEN:
                body = body[:_MAX_BODY_LEN]
            ts: str | None = None
            if fallback_date and ":" in time_part:
                bits = time_part.split(":")
                try:
                    h, mi = int(bits[0]), int(bits[1])
                    sec = int(bits[2]) if len(bits) > 2 else 0
                    ts = datetime(
                        fallback_date.year, fallback_date.month, fallback_date.day,
                        h, mi, sec,
                    ).isoformat()
                except (ValueError, IndexError):
                    ts = fallback_date.isoformat() if fallback_date else None
            raw_msgs.append((sender, body, ts))
        elif raw_msgs:
            sender, existing_body, existing_ts = raw_msgs[-1]
            existing_body += "\n" + line.strip()
            if len(existing_body) > _MAX_BODY_LEN:
                existing_body = existing_body[:_MAX_BODY_LEN]
            raw_msgs[-1] = (sender, existing_body, existing_ts)

    if not raw_msgs:
        return []

    messages: list[ChatMessage] = []
    for i, (sender, body, ts) in enumerate(raw_msgs):
        messages.append(_make_message(
            sender, _normalize_handle(sender),
            ts, body,
            source_str, file_relpath, 0, i,
        ))

    return [ChatSession(
        protocol="unknown",
        my_handle=None,
        remote_handle=None,
        start_ts=messages[0].date_sent if messages else None,
        end_ts=messages[-1].date_sent if messages else None,
        session_date=fallback_date.date().isoformat() if fallback_date else None,
        messages=messages,
    )]


def parse_file(file_path: Path, root: Path) -> list[ChatSession]:
    """Parse a single chat log file, returning sessions with ChatMessage records."""
    try:
        head = file_path.read_bytes()[:8192]
    except (OSError, PermissionError):
        return []

    fmt = detect_format(file_path, head)
    if fmt in ("unknown", "events_log"):
        return []

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    source_str = str(root)
    file_relpath = str(file_path.relative_to(root))
    file_date = infer_filename_date(file_path)
    if not file_date:
        file_date = get_canonical_mtime(file_path)

    if fmt == "aim_html":
        s = _parse_aim_html(content, file_path, file_date, source_str, file_relpath)
        return [s] if s else []
    if fmt == "plaintext":
        return _parse_plaintext_log(content, file_path, file_date, source_str, file_relpath)
    if fmt == "bracketed_time":
        return _parse_bracketed_time_log(content, file_path, file_date, source_str, file_relpath)
    return []


def discover_files(root: Path, include_pattern: re.Pattern[str] | None = None) -> list[Path]:
    """Find all supported chat log files under root."""
    files = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in _SUPPORTED_EXTS:
            if include_pattern and not include_pattern.search(str(p.relative_to(root))):
                continue
            files.append(p)
    return sorted(files)
