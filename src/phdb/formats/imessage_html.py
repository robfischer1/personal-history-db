"""iMessage HTML format parser — yields ChatMessage records from imessage-exporter output.

Source: a directory of .html files produced by ``imessage-exporter``, one per
contact or group chat.  Filenames encode participants (comma-separated).

Two-pass ordering: 1-on-1 files first (to build a contact display-name to phone
lookup), then group files (which consume the lookup).

Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from phdb.records import Attachment, ChatMessage, Provenance, Recipient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SHORT_CODE_RE = re.compile(r"^\+?\d{3,7}$")
_TS_RE = re.compile(r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)")

_SPAM_DOMAINS = {"galaxyhit.com", "zzz28.cn", "o4rnupex.asia"}
_KNOWN_AUTOMATED = {
    "verizon", "info@orders.apple.com", "noreply@orders.apple.com",
    "do_not_reply", "no-reply", "noreply",
}

_SNIPPET_LEN = 280
_MAX_BODY_LEN = 50_000

# ---------------------------------------------------------------------------
# Helpers (pure, no DB, no identity)
# ---------------------------------------------------------------------------


def normalize_addr(addr: str) -> str:
    """Lowercase-strip an address string."""
    return (addr or "").strip().lower()


def parse_filename_participants(filename: str) -> list[str]:
    """Extract normalized participant addresses from an imessage-exporter filename."""
    stem = Path(filename).stem
    parts = [p.strip() for p in stem.split(",")]
    return [normalize_addr(p) for p in parts if p]


def is_short_code(addr: str) -> bool:
    """True if *addr* looks like a numeric short code (3-7 digits)."""
    return bool(_SHORT_CODE_RE.match(addr or ""))


def parse_timestamp(ts_text: str) -> str | None:
    """Parse an imessage-exporter timestamp string to ISO-8601, or None."""
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


def is_bulk_sender(addr: str) -> tuple[bool, str | None]:
    """Classify an address as bulk/automated.  Returns (is_bulk, signal_tag)."""
    a = (addr or "").lower()
    if not a:
        return False, None
    if is_short_code(a):
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


# ---------------------------------------------------------------------------
# Single-message-div parser
# ---------------------------------------------------------------------------


def parse_message_block(msg_div: Tag) -> dict[str, object] | None:
    """Extract fields from a single ``.message`` div.  Returns None if empty."""
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
    ts_iso = parse_timestamp(ts_el.get_text(strip=True)) if ts_el else None

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


# ---------------------------------------------------------------------------
# Per-file parser — yields ChatMessage records
# ---------------------------------------------------------------------------


def parse_file(
    html_path: Path,
    *,
    owner_phone: str | None = None,
    name_to_phone: dict[str, str] | None = None,
    owner_names: set[str] | None = None,
) -> Iterator[ChatMessage]:
    """Parse one imessage-exporter HTML file, yielding ChatMessage records.

    Parameters
    ----------
    html_path : Path
        Path to the ``.html`` file.
    owner_phone : str or None
        The vault owner's phone number (used to fill sender_address on sent
        messages).  Passed in from identity config by the adapter.
    name_to_phone : dict or None
        Mutable lookup mapping display-names to phone numbers.  Updated in
        place for 1-on-1 files so group-file parsing can resolve names.
    owner_names : set or None
        Lowercase owner display-names to exclude from the lookup.
    """
    if name_to_phone is None:
        name_to_phone = {}
    if owner_names is None:
        owner_names = set()

    participants = parse_filename_participants(html_path.name)
    is_group = len(participants) > 1
    thread_key = ",".join(sorted(participants))

    src = html_path.read_bytes()
    raw_size = len(src)
    soup = BeautifulSoup(src, "lxml")
    msg_divs = soup.select("div.message")

    source_str = str(html_path)
    other_party_phone = participants[0] if (not is_group and participants) else None

    for msg_idx, div in enumerate(msg_divs):
        info = parse_message_block(div)
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
                    and sender_name not in name_to_phone
                ):
                    name_to_phone[sender_name] = other_party_phone
            else:
                if sender_name and sender_name in name_to_phone:
                    sender_addr = name_to_phone[sender_name]
                elif sender_name and (
                    "@" in sender_name
                    or (sender_name.startswith("+") and sender_name[1:].replace(" ", "").isdigit())
                ):
                    sender_addr = normalize_addr(sender_name)

        # Build recipients list
        recipients: list[Recipient] = []
        if direction_str == "sent":
            for p in participants:
                recipients.append(Recipient(address=p, name="", rtype="to"))
        else:
            if owner_phone:
                recipients.append(Recipient(address=owner_phone, name="Me", rtype="to"))
            if is_group:
                for p in participants:
                    if p != sender_addr:
                        recipients.append(Recipient(address=p, name="", rtype="to"))

        body_text: str | None = str(info["body_text"]) if info["body_text"] else None
        bulk_flag, _bulk_sig = is_bulk_sender(sender_addr) if sender_addr else (False, None)
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

        attachments: list[Attachment] = []
        for a in att_list:
            attachments.append(Attachment(
                provenance=Provenance(
                    source_path=source_str,
                    raw_hash=raw_hash,
                ),
                parent_id=f"imessage:{raw_hash}",
                filename=a.get("filename"),
                content_type=a.get("content_type"),
                size_bytes=a.get("size_bytes"),  # type: ignore[arg-type]
            ))

        yield ChatMessage(
            provenance=Provenance(
                source_path=source_str,
                raw_hash=raw_hash,
                source_byte_offset=msg_idx,
                source_byte_length=raw_size,
            ),
            sender_address=sender_addr or "",
            sender_name=sender_name,
            date_sent=str(info["date_sent"]) if info["date_sent"] else "",
            body_text=body_text,
            is_multipart=bool(info["is_multipart"]),
            has_attachments=bool(att_list),
            attachment_count=len(att_list),
            platform_id=f"imessage:{raw_hash}",
            thread_key=thread_key,
            recipients=tuple(recipients),
            attachments=tuple(attachments),
        )


# ---------------------------------------------------------------------------
# Directory discovery + ordering
# ---------------------------------------------------------------------------


def discover_html_files(source_path: Path) -> tuple[list[Path], list[Path]]:
    """Discover and split imessage-exporter HTML files into (one_on_one, groups).

    Returns two sorted lists: 1-on-1 files first, then group files.  The caller
    should process 1-on-1 files first to build the name-to-phone lookup.
    """
    all_files = sorted(f for f in source_path.iterdir() if f.suffix.lower() == ".html")
    one_on_one = [f for f in all_files if "," not in f.stem]
    groups = [f for f in all_files if "," in f.stem]
    return one_on_one, groups
