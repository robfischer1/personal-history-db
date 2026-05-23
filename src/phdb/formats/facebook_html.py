"""Facebook HTML format parser — yields records from Facebook Data Export zips.

Dispatches to subtree parsers:
- messages/inbox|filtered|requests → ChatMessage
- posts/ → SocialPost
- comments_and_reactions, groups, events, marketplace → SocialPost | Reaction

Pure parser: no DB, no identity.
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

from phdb.records import ChatMessage, Provenance, Reaction, SocialPost

FacebookRecord = ChatMessage | SocialPost | Reaction

_MAX_BODY_LEN = 50_000

_INBOX_PATTERNS = [
    "your_facebook_activity/messages/inbox/",
    "your_facebook_activity/messages/filtered_threads/",
    "your_facebook_activity/messages/message_requests/",
]

_POSTS_PATTERN = "your_facebook_activity/posts/"
_POSTS_THREAD_BUCKETS: dict[str, str] = {
    "your_posts__check_ins__photos_and_videos_1.html": "Posts",
    "archive.html": "Archive",
    "check-ins.html": "Check-ins",
    "places_you_have_been_tagged_in.html": "Tagged Places",
    "your_photos.html": "Your Photos",
    "1.html": "Profile Pictures",
    "0.html": "Photos",
}

ZIP_SUBTREE = "your_facebook_activity/"

RESIDUAL_TARGETS: list[tuple[str, str, str, bool]] = [
    ("comments_and_reactions/comments.html", "comment", "fb-comment", False),
    ("comments_and_reactions/likes_and_reactions.html", "reaction", "fb-reaction-mktp", True),
    ("comments_and_reactions/likes_and_reactions_1.html", "reaction", "fb-reaction", False),
    ("groups/your_comments_in_groups.html", "group-comment", "fb-group-comment", False),
    ("groups/group_posts_and_comments.html", "group-post", "fb-group-post", False),
    ("groups/your_group_membership_activity.html", "join", "fb-group-membership", False),
    ("events/event_invitations.html", "invite", "fb-event-invite", False),
    ("facebook_marketplace/conversations_you_had_as_a_buyer.html", "marketplace", "fb-mktp-buyer", True),
]


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

_FB_TS_STRICT_RE = re.compile(
    r"^([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+(?:am|pm))$"
)

_FB_TS_LEAD_RE = re.compile(
    r"\b(\w{3} \d{1,2}, \d{4} \d{1,2}:\d{2}(?::\d{2})? [ap]m)\b",
    re.IGNORECASE,
)

_FB_TS_FORMATS = [
    "%b %d, %Y %I:%M:%S %p",
    "%b %d, %Y %I:%M %p",
]


def _parse_fb_timestamp_strict(text: str | None) -> str | None:
    if not text:
        return None
    s = text.strip()
    if not _FB_TS_STRICT_RE.match(s):
        return None
    try:
        return datetime.strptime(s, "%b %d, %Y %I:%M:%S %p").isoformat()
    except ValueError:
        return None


def _parse_fb_timestamp_lenient(ts_str: str | None) -> str | None:
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


# ---------------------------------------------------------------------------
# Messenger threads → ChatMessage
# ---------------------------------------------------------------------------

def _list_thread_paths(zf: zipfile.ZipFile) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for name in zf.namelist():
        if not name.endswith("/message_1.html"):
            continue
        for cat_prefix in _INBOX_PATTERNS:
            if name.startswith(cat_prefix):
                rest = name[len(cat_prefix):]
                parts = rest.split("/")
                if len(parts) >= 2:
                    category = cat_prefix.split("/")[2]
                    thread_dir = parts[0]
                    result.append((category, thread_dir, name))
                break
    return sorted(result)


def _parse_messenger_html(html: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "lxml")
    messages: list[dict[str, object]] = []
    for sec in soup.select("section._a6-g"):
        h2 = sec.find("h2")
        sender_name = h2.get_text(strip=True) if h2 else None

        body_div = sec.select_one("div._a6-p")
        body_text: str | None = None
        if body_div:
            for d in body_div.find_all("div", recursive=False):
                inner = d.find_all("div", recursive=False)
                if inner:
                    for ii in inner:
                        t = ii.get_text(" ", strip=True)
                        if t:
                            body_text = t
                            break
                else:
                    t = d.get_text(" ", strip=True)
                    if t:
                        body_text = t
                if body_text:
                    break

        has_attachment = bool(sec.find("video") or sec.find("img") or sec.find("a", href=True))
        if has_attachment and not body_text:
            for el, kind in (
                (sec.find("video", src=True), "video"),
                (sec.find("img", src=True), "image"),
                (sec.find("a", href=True), "link"),
            ):
                if el is not None:
                    src = str(el.get("src") or el.get("href") or "")
                    body_text = f"[{kind}] {Path(src).name}" if src else f"[{kind}]"
                    break

        ts_div = sec.select_one("footer ._a72d")
        date_sent = _parse_fb_timestamp_strict(ts_div.get_text(strip=True)) if ts_div else None

        if not body_text and not has_attachment:
            continue
        if body_text and len(body_text) > _MAX_BODY_LEN:
            body_text = body_text[:_MAX_BODY_LEN]

        messages.append({
            "sender_name": sender_name,
            "date_sent": date_sent,
            "body_text": body_text,
            "has_attachment": has_attachment,
        })
    return messages


def _iter_messenger(
    zf: zipfile.ZipFile, source_str: str
) -> Iterator[ChatMessage]:
    threads = _list_thread_paths(zf)
    for fi, (category, thread_dir, html_path) in enumerate(threads):
        try:
            html = zf.read(html_path).decode("utf-8", errors="replace")
        except Exception:
            continue

        messages = _parse_messenger_html(html)

        for mi, m in enumerate(messages):
            body = m.get("body_text")
            if not body:
                continue
            body = str(body)
            sender_name = m.get("sender_name")
            sender_addr = (str(sender_name) if sender_name else "unknown").lower()
            date_sent = m.get("date_sent")
            has_att = bool(m.get("has_attachment"))

            dedup_seed = f"facebook|{html_path}|{mi}|{date_sent}|{sender_addr}|{body[:100]}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            yield ChatMessage(
                provenance=Provenance(
                    source_path=source_str,
                    raw_hash=raw_hash,
                    source_byte_offset=fi,
                    source_byte_length=mi,
                ),
                sender_address=sender_addr,
                sender_name=str(sender_name) if sender_name else None,
                date_sent=str(date_sent) if date_sent else "",
                body_text=body,
                has_attachments=has_att,
                attachment_count=1 if has_att else 0,
                thread_key=f"facebook:{category}:{thread_dir}",
            )


# ---------------------------------------------------------------------------
# Posts → SocialPost
# ---------------------------------------------------------------------------

def _parse_post_html(html: str) -> Iterator[dict[str, object]]:
    soup = BeautifulSoup(html, "lxml")
    for sec in soup.select("section._a6-g"):
        h2 = sec.find("h2")
        subject = h2.get_text(strip=True) if h2 else None

        body_div = sec.select_one("div._a6-p")
        body_text: str | None = None
        if body_div:
            body_text = body_div.get_text(" ", strip=True)
            body_text = re.sub(
                r"\s+Updated\s+[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+(?:am|pm)\s*$",
                "",
                body_text,
            )

        ts_div = sec.select_one("footer ._a72d")
        date_sent = _parse_fb_timestamp_strict(ts_div.get_text(strip=True)) if ts_div else None

        has_att = bool(sec.find("video") or sec.find("img") or sec.find("a", href=True))
        if has_att and not body_text:
            for el, kind in (
                (sec.find("video", src=True), "video"),
                (sec.find("img", src=True), "image"),
                (sec.find("a", href=True), "link"),
            ):
                if el is not None:
                    src = str(el.get("src") or el.get("href") or "")
                    body_text = f"[{kind}] {Path(src).name}" if src else f"[{kind}]"
                    break

        if not body_text and not subject:
            continue
        if body_text and len(body_text) > _MAX_BODY_LEN:
            body_text = body_text[:_MAX_BODY_LEN]

        yield {"subject": subject, "body_text": body_text, "date_sent": date_sent, "has_attachment": has_att}


def _iter_posts(
    zf: zipfile.ZipFile, source_str: str
) -> Iterator[SocialPost]:
    for fi, name in enumerate(sorted(zf.namelist())):
        if not name.startswith(_POSTS_PATTERN) or not name.endswith(".html"):
            continue
        filename = Path(name).name
        bucket = _POSTS_THREAD_BUCKETS.get(filename, "Other Posts")

        html = zf.read(name).decode("utf-8", errors="replace")
        for pi, post in enumerate(_parse_post_html(html)):
            body = str(post.get("body_text") or post.get("subject") or "[empty post]")
            date_sent = post.get("date_sent")
            has_att = bool(post.get("has_attachment"))

            dedup_seed = f"facebook-posts|{name}|{pi}|{date_sent}|{body[:100]}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            yield SocialPost(
                provenance=Provenance(
                    source_path=source_str,
                    raw_hash=raw_hash,
                    source_byte_offset=fi,
                    source_byte_length=pi,
                ),
                author_name="self",
                date_posted=str(date_sent) if date_sent else "",
                post_type="status",
                body_text=body,
                has_attachments=has_att,
                attachment_count=1 if has_att else 0,
                thread_key=f"facebook-posts:{bucket}",
                platform_id=f"facebook-posts:{raw_hash}",
            )


# ---------------------------------------------------------------------------
# Residuals → SocialPost | Reaction
# ---------------------------------------------------------------------------

def _outermost_a6g(soup: BeautifulSoup) -> list[Any]:
    all_a6g = soup.find_all(class_="_a6-g")
    return [b for b in all_a6g if not b.find_parent(class_="_a6-g")]


def _entry_footer_parts(block) -> tuple[str | None, str | None]:
    footer = block.find("footer", class_=re.compile(r"_a6-o"))
    timestamp_iso: str | None = None
    url: str | None = None
    if footer:
        ts_div = footer.find("div", class_="_a72d")
        if ts_div:
            timestamp_iso = _parse_fb_timestamp_lenient(ts_div.get_text(" ", strip=True))
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
            timestamp_iso = _parse_fb_timestamp_lenient(body)

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
                    timestamp_iso = _parse_fb_timestamp_lenient(v)
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

_REACTION_TYPES = {"reaction", "fb-reaction", "fb-reaction-mktp"}


def _iter_residuals(
    zf: zipfile.ZipFile, source_str: str
) -> Iterator[SocialPost | Reaction]:
    all_names = set(zf.namelist())

    for rel_path, post_type, kind, use_table in RESIDUAL_TARGETS:
        full = ZIP_SUBTREE + rel_path
        if full not in all_names:
            continue

        html_bytes = zf.read(full)
        parser_name = "table" if use_table else "h2"
        parser = _PARSERS[parser_name]
        entries = parser(html_bytes)

        is_reaction_kind = post_type == "reaction"

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

            prov = Provenance(source_path=source_str, raw_hash=raw_hash)
            date_str = entry["timestamp_iso"] or ""

            if is_reaction_kind:
                yield Reaction(
                    provenance=prov,
                    parent_id=raw_hash,
                    reactor_name="self",
                    reaction_type=entry["title"] or "like",
                    date_reacted=date_str,
                    target_summary=entry["body"],
                )
            else:
                yield SocialPost(
                    provenance=prov,
                    author_name="self",
                    date_posted=date_str,
                    post_type=post_type,
                    body_text=body_text,
                    thread_key=f"facebook:{kind}",
                    platform_id=msg_id,
                )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(source_path: Path) -> Iterator[FacebookRecord]:
    """Parse a Facebook export zip, yielding typed records.

    Dispatches to subtrees: messenger → ChatMessage, posts → SocialPost,
    residuals → SocialPost | Reaction.
    """
    source_str = str(source_path)

    with zipfile.ZipFile(source_path) as zf:
        yield from _iter_messenger(zf, source_str)
        yield from _iter_posts(zf, source_str)
        yield from _iter_residuals(zf, source_str)
