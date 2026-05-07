"""iMessage adapter — ingests imessage-exporter HTML output.

Source: a directory of .html files produced by `imessage-exporter`, one per
contact or group chat. Filenames encode participants (comma-separated).

Two-pass strategy:
  Pass 1: 1-on-1 files — build a contact display-name → phone lookup.
  Pass 2: group files — resolve display names via the lookup.

Threads are created per conversation file (keyed on sorted participants).
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

from bs4 import BeautifulSoup, Tag

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.imessage")

_SHORT_CODE_RE = re.compile(r"^\+?\d{3,7}$")
_TS_RE = re.compile(r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)")

_SPAM_DOMAINS = {"galaxyhit.com", "zzz28.cn", "o4rnupex.asia"}
_KNOWN_AUTOMATED = {
    "verizon", "info@orders.apple.com", "noreply@orders.apple.com",
    "do_not_reply", "no-reply", "noreply",
}

_SNIPPET_LEN = 280
_MAX_BODY_LEN = 50_000


def _normalize_addr(addr: str) -> str:
    return (addr or "").strip().lower()


def _parse_filename_participants(filename: str) -> list[str]:
    stem = Path(filename).stem
    parts = [p.strip() for p in stem.split(",")]
    return [_normalize_addr(p) for p in parts if p]


def _is_short_code(addr: str) -> bool:
    return bool(_SHORT_CODE_RE.match(addr or ""))


def _parse_timestamp(ts_text: str) -> str | None:
    if not ts_text:
        return None
    m = _TS_RE.search(ts_text)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%b %d, %Y %I:%M:%S %p")
        return dt.isoformat()
    except ValueError:
        return None


def _is_bulk_sender(addr: str) -> tuple[bool, str | None]:
    a = (addr or "").lower()
    if not a:
        return False, None
    if _is_short_code(a):
        return True, "short-code"
    if a in _KNOWN_AUTOMATED:
        return True, "known-automated"
    if "@" in a:
        domain = a.split("@", 1)[1]
        if domain in _SPAM_DOMAINS:
            return True, "spam-domain"
        if domain.startswith("orders.") or domain.startswith("noreply"):
            return True, "automated-domain"
    if a.startswith("noreply") or a.startswith("no-reply") or a.startswith("donotreply"):
        return True, "noreply-pattern"
    return False, None


def _parse_message_block(msg_div: Tag) -> dict[str, object] | None:
    """Extract fields from a single .message div. Returns None if empty."""
    direction = "sent" if msg_div.select_one(".sent") else (
        "received" if msg_div.select_one(".received") else "unknown"
    )

    sender_name: str | None = None
    for s in msg_div.select(".sender"):
        in_reply = False
        for parent in s.parents:
            if parent is msg_div:
                break
            if isinstance(parent, Tag) and "class" in parent.attrs:
                cls = parent.get("class")
                if isinstance(cls, list) and ("reply" in cls or "reply_context" in cls):
                    in_reply = True
                    break
        if not in_reply:
            sender_name = s.get_text(strip=True)
            break

    if direction == "sent":
        sender_name = "Me"

    ts_el = msg_div.select_one(".timestamp")
    ts_iso = _parse_timestamp(ts_el.get_text(strip=True)) if ts_el else None

    parts = msg_div.select(".message_part")
    body_text: str | None = None
    if parts:
        texts = [p.get_text(" ", strip=True) for p in parts]
        body_text = " ".join(t for t in texts if t).strip() or None
        if body_text and len(body_text) > _MAX_BODY_LEN:
            body_text = body_text[:_MAX_BODY_LEN]

    attachments: list[dict[str, str | None]] = []
    for a in msg_div.select(".attachment"):
        href: str | None = None
        link = a.find("a", href=True)
        if isinstance(link, Tag):
            href = str(link["href"])
        else:
            img = a.find("img", src=True)
            if isinstance(img, Tag):
                href = str(img["src"])
        att_text = a.get_text(" ", strip=True) or None
        attachments.append({
            "filename": Path(href).name if href else (att_text[:120] if att_text else None),
            "content_type": None,
            "size_bytes": None,
        })

    if not body_text and not attachments:
        return None

    return {
        "direction": direction,
        "sender_name": sender_name,
        "date_sent": ts_iso,
        "body_text": body_text,
        "attachments": attachments,
        "is_multipart": len(parts) > 1,
    }


class IMessageAdapter(Adapter):
    """Ingest imessage-exporter HTML directories."""

    name = "imessage"
    source_kind = "imessage"
    file_kind = "html"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def __init__(
        self,
        *,
        max_seconds: float | None = None,
    ) -> None:
        self.max_seconds = max_seconds
        self._name_to_phone: dict[str, str] = {}

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        return _is_bulk_sender(row.sender_address) if row.sender_address else (False, None)

    def compute_raw_hash(self, row: AdapterRow) -> str:
        seed = (
            f"{row.thread_key or ''}|{row.source_byte_offset or 0}"
            f"|{row.date_sent or ''}|{row.sender_address or ''}"
            f"|{(row.body_text or '')[:100]}"
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        """Not used directly — run() drives per-file iteration."""
        yield from ()

    def _iter_file_rows(
        self,
        html_path: Path,
        owner_phones: set[str],
        owner_names: set[str],
    ) -> Iterator[AdapterRow]:
        """Parse one HTML file and yield AdapterRows."""
        participants = _parse_filename_participants(html_path.name)
        is_group = len(participants) > 1
        thread_key = ",".join(sorted(participants))

        src = html_path.read_bytes()
        raw_size = len(src)
        soup = BeautifulSoup(src, "lxml")
        msg_divs = soup.select("div.message")

        owner_phone = next(iter(owner_phones), None)
        other_party_phone = participants[0] if (not is_group and participants) else None

        for msg_idx, div in enumerate(msg_divs):
            info = _parse_message_block(div)
            if info is None:
                continue

            sender_addr: str | None = None
            sender_name: str | None = str(info["sender_name"]) if info["sender_name"] else None
            direction_str = str(info["direction"])

            if direction_str == "sent":
                sender_addr = owner_phone
                if not sender_name:
                    sender_name = "Me"
            else:
                if not is_group and other_party_phone:
                    sender_addr = other_party_phone
                    if (
                        sender_name
                        and sender_name.lower() not in owner_names
                        and sender_name not in self._name_to_phone
                    ):
                        self._name_to_phone[sender_name] = other_party_phone
                else:
                    if sender_name and sender_name in self._name_to_phone:
                        sender_addr = self._name_to_phone[sender_name]
                    elif sender_name and (
                        "@" in sender_name
                        or (sender_name.startswith("+") and sender_name[1:].replace(" ", "").isdigit())
                    ):
                        sender_addr = _normalize_addr(sender_name)

            recipients: list[dict[str, str]] = []
            if direction_str == "sent":
                for p in participants:
                    recipients.append({"address": p, "name": "", "rtype": "to"})
            else:
                if owner_phone:
                    recipients.append({"address": owner_phone, "name": "Me", "rtype": "to"})
                if is_group:
                    for p in participants:
                        if p != sender_addr:
                            recipients.append({"address": p, "name": "", "rtype": "to"})

            sender_domain: str | None = None
            if sender_addr and "@" in sender_addr:
                sender_domain = sender_addr.split("@", 1)[1]

            body_text: str | None = str(info["body_text"]) if info["body_text"] else None
            bulk_flag, bulk_sig = _is_bulk_sender(sender_addr) if sender_addr else (False, None)
            if bulk_flag and body_text and len(body_text) > _SNIPPET_LEN:
                body_text = body_text[:_SNIPPET_LEN]

            raw_hash = hashlib.sha256(
                f"{thread_key}|{msg_idx}|{info['date_sent']}|{sender_addr}|{(body_text or '')[:100]}".encode()
            ).hexdigest()

            direction = (
                "outbound" if direction_str == "sent"
                else ("inbound" if direction_str == "received" else "unknown")
            )

            raw_atts = info["attachments"]
            att_list: list[dict[str, str | None]] = list(raw_atts) if isinstance(raw_atts, list) else []

            yield AdapterRow(
                schema_type="Message",
                rfc822_message_id=f"imessage:{raw_hash}",
                sender_address=sender_addr,
                sender_name=sender_name,
                sender_domain=sender_domain,
                direction=direction,
                date_sent=str(info["date_sent"]) if info["date_sent"] else None,
                body_text=body_text,
                body_text_source="imessage-html",
                is_multipart=int(bool(info["is_multipart"])),
                has_attachments=int(bool(att_list)),
                attachment_count=len(att_list),
                is_bulk=int(bulk_flag),
                bulk_signal=bulk_sig,
                source_byte_offset=msg_idx,
                source_byte_length=raw_size,
                raw_hash=raw_hash,
                recipients=recipients,
                attachments=[
                    {"filename": a.get("filename"), "content_type": a.get("content_type"),
                     "content_disposition": None, "size_bytes": a.get("size_bytes"),
                     "on_disk_path": None, "content_hash": None}
                    for a in att_list
                ],
                thread_key=thread_key,
            )

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
        self, conn: sqlite3.Connection, source_file_id: int, filename: str
    ) -> None:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        done = set(json.loads(row[0])) if row and row[0] else set()
        done.add(filename)
        conn.execute(
            "UPDATE source_files SET notes = ? WHERE id = ?",
            (json.dumps(sorted(done)), source_file_id),
        )

    def _rebuild_name_lookup(
        self, conn: sqlite3.Connection, source_file_id: int
    ) -> None:
        """Rebuild name→phone lookup from previously ingested rows (for resume)."""
        for r in conn.execute(
            """SELECT sender_name, sender_address FROM messages
               WHERE sender_address IS NOT NULL AND sender_name IS NOT NULL
                 AND sender_address LIKE '+%' AND source_file_id = ?
               GROUP BY sender_name""",
            (source_file_id,),
        ):
            self._name_to_phone[r[0]] = r[1]

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

        owner_phones = settings.identity.owner_phones
        owner_names = settings.identity.owner_names

        all_files = sorted(f for f in source_path.iterdir() if f.suffix.lower() == ".html")
        one_on_one = [f for f in all_files if "," not in f.stem]
        groups = [f for f in all_files if "," in f.stem]
        ordered = one_on_one + groups

        done_files = self._get_done_files(conn, source_file_id)
        todo = [f for f in ordered if f.name not in done_files]
        log.info(
            "[%s] Files: %d total (%d 1-on-1, %d group), %d done, %d remaining",
            self.name, len(all_files), len(one_on_one), len(groups),
            len(done_files), len(todo),
        )

        self._name_to_phone = {}
        self._rebuild_name_lookup(conn, source_file_id)
        if self._name_to_phone:
            log.info("[%s] Resumed contact lookup with %d entries", self.name, len(self._name_to_phone))

        has_identity = bool(owner_names or settings.identity.owner_emails or owner_phones or settings.identity.owner_handles)
        t_start = time.time()
        files_done = 0
        touched_threads: set[int] = set()

        for html_file in todo:
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[%s] Time budget reached after %d files", self.name, files_done)
                break

            try:
                for row in self._iter_file_rows(html_file, owner_phones, owner_names):
                    report.rows_yielded += 1

                    if row.body_text and not row.body_text_hash:
                        row.body_text_hash = hashlib.sha256(row.body_text.encode("utf-8")).hexdigest()

                    if row.direction == "unknown" and has_identity:
                        row.direction = self.infer_direction(row, settings.identity)

                    message_id = self._insert_row(conn, row, source_file_id)
                    if message_id is None:
                        report.rows_skipped += 1
                        continue

                    report.rows_inserted += 1
                    self._insert_sidecars(conn, message_id, row)

                    if row.thread_key:
                        participants = _parse_filename_participants(html_file.name)
                        thread_id, created = self._upsert_thread(conn, row.thread_key, participants)
                        self._link_message_thread(conn, message_id, thread_id)
                        if created:
                            report.threads_created += 1
                        touched_threads.add(thread_id)

                self._mark_file_done(conn, source_file_id, html_file.name)
                conn.commit()
                files_done += 1

            except Exception:
                log.exception("[%s] Error processing %s", self.name, html_file.name)
                report.errors.append(html_file.name)

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
