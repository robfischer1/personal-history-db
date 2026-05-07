"""Facebook residuals adapter — comments, reactions, groups, events, marketplace.

Source: Facebook export zip containing HTML files under your_facebook_activity/.
Two parse patterns: "h2" (section with h2+body+footer) and "table" (key-value
table rows). Each entry becomes a messages row with thread_key=facebook:{kind}.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.facebook_residuals")

ZIP_SUBTREE = "your_facebook_activity/"

TARGETS: list[tuple[str, str, str, int, str]] = [
    ("comments_and_reactions/comments.html", "Comment", "fb-comment", 0, "h2"),
    ("comments_and_reactions/likes_and_reactions.html", "LikeAction", "fb-reaction-mktp", 1, "table"),
    ("comments_and_reactions/likes_and_reactions_1.html", "LikeAction", "fb-reaction", 1, "h2"),
    ("groups/your_comments_in_groups.html", "Comment", "fb-group-comment", 0, "h2"),
    ("groups/group_posts_and_comments.html", "SocialMediaPosting", "fb-group-post", 0, "h2"),
    ("groups/your_group_membership_activity.html", "JoinAction", "fb-group-membership", 1, "h2"),
    ("events/event_invitations.html", "InviteAction", "fb-event-invite", 0, "h2"),
    ("facebook_marketplace/conversations_you_had_as_a_buyer.html", "Conversation", "fb-mktp-buyer", 0, "table"),
]

_FB_TS_LEAD_RE = re.compile(
    r"\b(\w{3} \d{1,2}, \d{4} \d{1,2}:\d{2}(?::\d{2})? [ap]m)\b",
    re.IGNORECASE,
)

_FB_TS_FORMATS = [
    "%b %d, %Y %I:%M:%S %p",
    "%b %d, %Y %I:%M %p",
]


def _parse_fb_timestamp(ts_str: str | None) -> str | None:
    if not ts_str:
        return None
    m = _FB_TS_LEAD_RE.search(ts_str)
    if not m:
        return None
    candidate = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    candidate = candidate.replace(" am", " AM").replace(" pm", " PM")
    for fmt in _FB_TS_FORMATS:
        try:
            dt = datetime.strptime(candidate, fmt)
            return dt.replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    return None


def _outermost_a6g(soup: BeautifulSoup) -> list[Any]:
    all_a6g = soup.find_all(class_="_a6-g")
    return [b for b in all_a6g if not b.find_parent(class_="_a6-g")]


def _entry_footer_parts(block: Any) -> tuple[str | None, str | None]:
    footer = block.find("footer", class_=re.compile(r"_a6-o"))
    timestamp_iso: str | None = None
    url: str | None = None
    if footer:
        ts_div = footer.find("div", class_="_a72d")
        if ts_div:
            timestamp_iso = _parse_fb_timestamp(ts_div.get_text(" ", strip=True))
        link = footer.find("a", href=True)
        if link:
            url = link["href"]
    return timestamp_iso, url


def _parse_h2_section(html_bytes: bytes) -> list[dict[str, str | None]]:
    soup = BeautifulSoup(html_bytes, "lxml")
    for tag in soup(["style", "script"]):
        tag.decompose()

    entries: list[dict[str, str | None]] = []
    for block in _outermost_a6g(soup):
        h2 = block.find("h2", class_=re.compile(r"_a6-h"))
        title = h2.get_text(" ", strip=True) if h2 else None

        body: str | None = None
        for body_div in block.find_all("div", class_=re.compile(r"_a6-p")):
            if body_div.find_parent("footer"):
                continue
            txt = body_div.get_text(" ", strip=True)
            if txt:
                body = txt
                break

        timestamp_iso, url = _entry_footer_parts(block)

        if timestamp_iso is None and body:
            timestamp_iso = _parse_fb_timestamp(body)

        if title or body:
            entries.append({
                "title": title,
                "body": body,
                "url": url,
                "timestamp_iso": timestamp_iso,
            })
    return entries


def _parse_table_section(html_bytes: bytes) -> list[dict[str, str | None]]:
    soup = BeautifulSoup(html_bytes, "lxml")
    for tag in soup(["style", "script"]):
        tag.decompose()

    entries: list[dict[str, str | None]] = []
    for block in _outermost_a6g(soup):
        kvs: list[tuple[str, str]] = []
        for tbl in block.find_all("table"):
            for row in tbl.find_all("tr"):
                k_el = row.find("td", class_=re.compile(r"_a6_q"))
                v_el = row.find("td", class_=re.compile(r"_a6_r"))
                if k_el and v_el:
                    k = k_el.get_text(" ", strip=True)
                    v = v_el.get_text(" ", strip=True)
                    if k or v:
                        kvs.append((k, v))

        if not kvs:
            continue

        title: str | None = None
        for k, v in kvs:
            if k.lower() in {"title", "product title", "product name", "reaction"}:
                title = v
                break
        if title is None and kvs:
            title = kvs[0][1]

        body = "\n".join(f"{k}: {v}" for k, v in kvs)

        timestamp_iso, url = _entry_footer_parts(block)
        if timestamp_iso is None:
            for k, v in kvs:
                if k.lower() in {"time", "update time", "timestamp"}:
                    timestamp_iso = _parse_fb_timestamp(v)
                    if timestamp_iso:
                        break

        entries.append({
            "title": title,
            "body": body,
            "url": url,
            "timestamp_iso": timestamp_iso,
        })
    return entries


_PARSERS = {"h2": _parse_h2_section, "table": _parse_table_section}


class FacebookResidualsAdapter(Adapter):
    """Ingest Facebook residual data: comments, reactions, groups, events, marketplace."""

    name = "facebook_residuals"
    source_kind = "facebook-residuals"
    file_kind = "zip"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        with zipfile.ZipFile(source_path) as zf:
            all_names = set(zf.namelist())
            for rel_path, schema_type, kind, is_bulk, parser_name in TARGETS:
                full = ZIP_SUBTREE + rel_path
                if full not in all_names:
                    log.debug("Not found in zip: %s", full)
                    continue

                html_bytes = zf.read(full)
                parser = _PARSERS[parser_name]
                entries = parser(html_bytes)

                for entry in entries:
                    body_parts: list[str] = []
                    if entry["title"]:
                        body_parts.append(entry["title"])
                    if entry["body"]:
                        body_parts.append(entry["body"])
                    if entry["url"] and entry["url"] not in body_parts:
                        body_parts.append(entry["url"])
                    body_text = "\n".join(body_parts).strip()
                    if not body_text:
                        continue

                    content_hash = hashlib.sha1(
                        body_text.encode("utf-8")[:200]
                    ).hexdigest()[:16]
                    msg_id = f"facebook:{kind}:{content_hash}"
                    raw_hash = hashlib.sha256(msg_id.encode("utf-8")).hexdigest()

                    subject = (entry["title"] or "")[:200] or kind

                    yield AdapterRow(
                        schema_type=schema_type,
                        rfc822_message_id=msg_id,
                        subject=subject,
                        sender_address="facebook:rob",
                        sender_name="Rob (Facebook)",
                        sender_domain="facebook",
                        direction="outbound",
                        date_sent=entry["timestamp_iso"],
                        body_text=body_text,
                        body_text_source="facebook-html",
                        is_bulk=is_bulk,
                        raw_hash=raw_hash,
                        body_text_hash=hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
                        thread_key=f"facebook:{kind}",
                    )
