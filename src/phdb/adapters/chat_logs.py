"""Chat logs adapter — ingests legacy IM chat logs (AIM, MSN, Trillian, Yahoo).

Source: a directory tree containing chat-log files in three formats:

  1. AIM HTML logs (.htm) — AOL Instant Messenger native export. Color-coded
     <SPAN>/<FONT> blocks per message.

  2. Plaintext session logs (.log / .txt) — Trillian-derived format used by
     MSN, Yahoo, and per-handle AIM exports. Format:
        Session Start (PROTO - my_handle:remote_handle): Mon Jul 14 14:00:00 2003
        Handle: message text
        Session Close (remote_handle): Mon Jul 14 14:13:30 2003

  3. Bracketed-time logs — ``[HH:MM] Sender: msg`` format from saved-as-text.

Per-file resume: completed relative paths are tracked in source_files.notes
as a JSON list. One source_files row covers the whole chat-logs root directory.

Thread key: ``{proto}:{my}:{remote}:{start_ts}:{path_hash}`` — one
Conversation per session.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.chat_logs")

_MAX_BODY_LEN = 50_000
_SUPPORTED_EXTS = {".htm", ".html", ".txt", ".log"}
_LOG_EVERY_FILES = 25

# ---- Regex ------------------------------------------------------------------

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

# ---- Helpers ----------------------------------------------------------------


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


def _infer_filename_date(file_path: Path) -> datetime | None:
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


def _get_canonical_mtime(file_path: Path) -> datetime | None:
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


def _detect_format(file_path: Path, head_bytes: bytes) -> str:
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


# ---- Session dict typedefs (just dicts in practice) -------------------------

SessionMsg = dict[str, str | None | bool]
SessionDict = dict[str, object]


# ---- Parsers ----------------------------------------------------------------


def _parse_aim_html(
    content: str, file_path: Path, file_date: datetime | None
) -> SessionDict | None:
    parts = file_path.parts
    rob_handle: str | None = None
    remote_handle: str | None = None
    if "AIM" in parts:
        idx = parts.index("AIM")
        if idx + 2 < len(parts):
            rob_handle = parts[idx + 1]
            remote_handle = parts[idx + 2]

    msgs = list(AIM_MSG_RE.finditer(content))
    if not msgs:
        return None

    parsed_msgs: list[SessionMsg] = []
    for m in msgs:
        sender = m.group("sender").strip()
        ts_local = m.group("ts").strip()
        body = _strip_html_tags(m.group("rest"))
        body = re.sub(r"^[\s:]+", "", body)
        if not body:
            continue
        if len(body) > _MAX_BODY_LEN:
            body = body[:_MAX_BODY_LEN]
        date_sent = _combine_date_and_time(file_date, ts_local)
        parsed_msgs.append({
            "sender_name": sender,
            "sender_address": _normalize_handle(sender),
            "date_sent": date_sent,
            "body_text": body,
        })

    if not parsed_msgs:
        return None

    return {
        "protocol": "aim",
        "my_handle": _normalize_handle(rob_handle),
        "remote_handle": _normalize_handle(remote_handle),
        "start_ts": parsed_msgs[0].get("date_sent"),
        "end_ts": parsed_msgs[-1].get("date_sent"),
        "session_date": file_date.date().isoformat() if file_date else None,
        "messages": parsed_msgs,
    }


def _parse_plaintext_log(
    content: str, file_path: Path, fallback_date: datetime | None
) -> list[SessionDict]:
    content = _strip_msn_color_codes(content)
    lines = content.split("\n")
    sessions: list[SessionDict] = []
    current: SessionDict | None = None

    def new_session(
        handle_str: str | None = None, start_ts_text: str | None = None
    ) -> SessionDict:
        proto, my, remote = _parse_session_handle(handle_str)
        return {
            "protocol": proto,
            "my_handle": my,
            "remote_handle": remote,
            "start_ts": _parse_session_timestamp(start_ts_text),
            "end_ts": None,
            "session_date": None,
            "messages": [],
            "system_events": [],
        }

    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            continue
        m_open = SESSION_OPEN_RE.match(line)
        if m_open:
            if current:
                msgs = current.get("messages", [])
                evts = current.get("system_events", [])
                if (isinstance(msgs, list) and msgs) or (isinstance(evts, list) and evts):
                    sessions.append(current)
            current = new_session(m_open.group(1), m_open.group(2))
            continue
        m_open_bare = SESSION_OPEN_BARE_RE.match(line)
        if m_open_bare:
            if current:
                msgs = current.get("messages", [])
                evts = current.get("system_events", [])
                if (isinstance(msgs, list) and msgs) or (isinstance(evts, list) and evts):
                    sessions.append(current)
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
            sessions.append(current)
            current = None
            continue
        m_sys = SYSTEM_LINE_RE.match(line)
        if m_sys:
            if current is None:
                current = new_session()
            evts = current.get("system_events")
            if isinstance(evts, list):
                evts.append(m_sys.group(1))
            continue
        m_msg = MSG_LINE_RE.match(line)
        if m_msg:
            if current is None:
                current = new_session()
            sender = m_msg.group(1).strip()
            body = m_msg.group(2)
            if len(body) > _MAX_BODY_LEN:
                body = body[:_MAX_BODY_LEN]
            msgs_list = current.get("messages")
            if isinstance(msgs_list, list):
                msgs_list.append({
                    "sender_name": sender,
                    "sender_address": _normalize_handle(sender),
                    "date_sent": None,
                    "body_text": body,
                })

    if current:
        msgs = current.get("messages", [])
        evts = current.get("system_events", [])
        if (isinstance(msgs, list) and msgs) or (isinstance(evts, list) and evts):
            sessions.append(current)

    for s in sessions:
        ts = s.get("start_ts")
        if not ts and fallback_date:
            ts = fallback_date.isoformat()
        s["session_date"] = (str(ts) or "")[:10] if ts else None
        msgs_list = s.get("messages")
        if isinstance(msgs_list, list):
            for m in msgs_list:
                if isinstance(m, dict):
                    m["date_sent"] = str(ts) if ts else None

    return sessions


def _parse_bracketed_time_log(
    content: str, file_path: Path, fallback_date: datetime | None
) -> list[SessionDict]:
    content = _strip_msn_color_codes(content)
    messages: list[SessionMsg] = []
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
            messages.append({
                "sender_name": sender,
                "sender_address": _normalize_handle(sender),
                "date_sent": ts,
                "body_text": body,
            })
        elif messages:
            last = messages[-1]
            existing = str(last.get("body_text") or "")
            existing += "\n" + line.strip()
            if len(existing) > _MAX_BODY_LEN:
                existing = existing[:_MAX_BODY_LEN]
            last["body_text"] = existing

    if not messages:
        return []

    return [{
        "protocol": "unknown",
        "my_handle": None,
        "remote_handle": None,
        "start_ts": messages[0].get("date_sent"),
        "end_ts": messages[-1].get("date_sent"),
        "session_date": fallback_date.date().isoformat() if fallback_date else None,
        "messages": messages,
        "system_events": [],
    }]


# ---- Adapter ----------------------------------------------------------------


class ChatLogsAdapter(Adapter):
    """Ingest legacy IM chat log directories (AIM/MSN/Trillian/Yahoo)."""

    name = "chat_logs"
    source_kind = "chat-logs"
    file_kind = "mixed"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def __init__(
        self,
        *,
        max_seconds: float | None = None,
        include_pattern: str | None = None,
    ) -> None:
        self.max_seconds = max_seconds
        self.include_pattern = re.compile(include_pattern) if include_pattern else None

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield from ()

    def compute_raw_hash(self, row: AdapterRow) -> str:
        seed = (
            f"{row.thread_key or ''}|{row.source_byte_offset or 0}"
            f"|{row.date_sent or ''}|{row.sender_address or ''}"
            f"|{(row.body_text or '')[:100]}"
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def _get_done_files(self, conn: sqlite3.Connection, source_file_id: int) -> set[str]:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        if not row or not row[0]:
            return set()
        try:
            return set(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            return set()

    def _mark_file_done(
        self, conn: sqlite3.Connection, source_file_id: int, relpath: str
    ) -> None:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        done = set(json.loads(row[0])) if row and row[0] else set()
        done.add(relpath)
        conn.execute(
            "UPDATE source_files SET notes = ? WHERE id = ?",
            (json.dumps(sorted(done)), source_file_id),
        )

    def _discover_files(self, root: Path) -> list[Path]:
        files = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in _SUPPORTED_EXTS:
                if self.include_pattern and not self.include_pattern.search(
                    str(p.relative_to(root))
                ):
                    continue
                files.append(p)
        return sorted(files)

    def _parse_file(
        self, file_path: Path, root: Path
    ) -> list[SessionDict]:
        try:
            head = file_path.read_bytes()[:8192]
        except (OSError, PermissionError):
            return []

        fmt = _detect_format(file_path, head)
        if fmt in ("unknown", "events_log"):
            return []

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        file_date = _infer_filename_date(file_path)
        if not file_date:
            file_date = _get_canonical_mtime(file_path)

        if fmt == "aim_html":
            s = _parse_aim_html(content, file_path, file_date)
            return [s] if s else []
        if fmt == "plaintext":
            return _parse_plaintext_log(content, file_path, file_date)
        if fmt == "bracketed_time":
            return _parse_bracketed_time_log(content, file_path, file_date)
        return []

    def _is_owner(self, handle: str | None, owner_names: set[str]) -> bool:
        if not handle:
            return False
        return handle.strip().lower() in owner_names

    def _iter_session_rows(
        self,
        session: SessionDict,
        file_relpath: str,
        session_index: int,
        file_index: int,
        owner_names: set[str],
    ) -> Iterator[AdapterRow]:
        proto = str(session.get("protocol") or "unknown")
        my = str(session.get("my_handle") or "unknown")
        remote = str(session.get("remote_handle") or "unknown")
        start = str(session.get("start_ts") or session.get("session_date") or "unknown")
        path_hash = hashlib.sha256(f"{file_relpath}#sess{session_index}".encode()).hexdigest()[:8]
        thread_key = f"{proto}:{my}:{remote}:{start}:{path_hash}"

        msgs_list = session.get("messages")
        if not isinstance(msgs_list, list):
            return

        for msg_idx, m in enumerate(msgs_list):
            if not isinstance(m, dict):
                continue
            body = str(m.get("body_text") or "")
            if not body:
                continue

            sender_name = str(m.get("sender_name") or "")
            sender_addr = str(m.get("sender_address") or "")

            if self._is_owner(sender_name, owner_names) or (sender_addr and sender_addr == my):
                direction = "outbound"
            elif sender_addr and sender_addr == remote:
                direction = "inbound"
            elif self._is_owner(sender_addr, owner_names):
                direction = "outbound"
            else:
                direction = "inbound"

            dedup_seed = f"{file_relpath}|{session_index}|{msg_idx}|{m.get('date_sent')}|{sender_addr}|{body[:100]}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()
            synthetic_id = f"chatlog:{raw_hash}"
            body_hash = hashlib.sha256(body.encode()).hexdigest()

            sender_domain: str | None = None
            if sender_addr and "@" in sender_addr:
                sender_domain = sender_addr.split("@", 1)[1]

            recipients: list[dict[str, str]] = []
            other = remote if direction == "outbound" else my
            if other and other != "unknown":
                recipients.append({"address": other, "name": "", "rtype": "to"})

            yield AdapterRow(
                schema_type="Message",
                rfc822_message_id=synthetic_id,
                sender_address=sender_addr or None,
                sender_name=sender_name or None,
                sender_domain=sender_domain,
                direction=direction,
                date_sent=str(m.get("date_sent")) if m.get("date_sent") else None,
                body_text=body,
                body_text_source="chat-log",
                is_multipart=0,
                has_attachments=0,
                attachment_count=0,
                is_bulk=0,
                source_byte_offset=file_index,
                source_byte_length=msg_idx,
                raw_hash=raw_hash,
                body_text_hash=body_hash,
                recipients=recipients,
                thread_key=thread_key,
            )

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

        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id
        log.info("[%s] Source registered: id=%d path=%s", self.name, source_file_id, source_path)

        owner_names = settings.identity.owner_names
        all_owner_ids = set(owner_names)
        for handles in settings.identity.owner_handles.values():
            all_owner_ids.update(handles)
        for email in settings.identity.owner_emails:
            all_owner_ids.add(email)
            local = email.split("@", 1)[0] if "@" in email else email
            all_owner_ids.add(local)

        all_files = self._discover_files(source_path)
        done_files = self._get_done_files(conn, source_file_id)
        todo = [f for f in all_files if str(f.relative_to(source_path)) not in done_files]

        log.info(
            "[%s] Files: %d discovered, %d done, %d remaining",
            self.name, len(all_files), len(done_files), len(todo),
        )

        t_start = time.time()
        files_done = 0
        touched_threads: set[int] = set()

        for fi, file_path in enumerate(todo):
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[%s] Time budget reached after %d files", self.name, files_done)
                break

            relpath = str(file_path.relative_to(source_path))
            try:
                sessions = self._parse_file(file_path, source_path)
                for sidx, session in enumerate(sessions):
                    for row in self._iter_session_rows(
                        session, relpath, sidx, fi, all_owner_ids,
                    ):
                        report.rows_yielded += 1

                        if row.body_text and not row.body_text_hash:
                            row.body_text_hash = hashlib.sha256(
                                row.body_text.encode("utf-8")
                            ).hexdigest()

                        message_id = self._insert_row(conn, row, source_file_id)
                        if message_id is None:
                            report.rows_skipped += 1
                            continue

                        report.rows_inserted += 1
                        self._insert_sidecars(conn, message_id, row)

                        if row.thread_key:
                            my_h = str(session.get("my_handle") or "unknown")
                            remote_h = str(session.get("remote_handle") or "unknown")
                            participants = sorted({h for h in [my_h, remote_h] if h})
                            thread_id, created = self._upsert_thread(
                                conn, row.thread_key, participants
                            )
                            self._link_message_thread(conn, message_id, thread_id)
                            if created:
                                report.threads_created += 1
                            touched_threads.add(thread_id)

                self._mark_file_done(conn, source_file_id, relpath)
                conn.commit()
                files_done += 1

            except Exception:
                log.exception("[%s] Error processing %s", self.name, relpath)
                report.errors.append(relpath)

            if files_done % _LOG_EVERY_FILES == 0 and files_done > 0:
                log.info(
                    "[%s] Progress: %d/%d files, %d rows inserted",
                    self.name, files_done, len(todo), report.rows_inserted,
                )

        for tid in touched_threads:
            self._update_thread_aggregates(conn, tid)

        actual = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (actual, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d files, %d yielded, %d inserted, %d skipped, %d threads",
            self.name, files_done, report.rows_yielded, report.rows_inserted,
            report.rows_skipped, report.threads_created,
        )
        return report
