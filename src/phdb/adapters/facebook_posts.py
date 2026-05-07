"""Facebook Posts adapter — ingests broadcasts from a Facebook Data Export zip.

Source: same facebook-*.zip as the facebook adapter. Posts HTML files under
your_facebook_activity/posts/. Each post becomes schema_type='SocialMediaPosting'.
All outbound. Per-bucket threads.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup

from phdb.adapters._facebook_utils import parse_fb_timestamp as _parse_fb_timestamp
from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.facebook_posts")

_MAX_BODY_LEN = 50_000
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
        date_sent = _parse_fb_timestamp(ts_div.get_text(strip=True)) if ts_div else None

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


class FacebookPostsAdapter(Adapter):
    """Ingest Facebook Posts (broadcasts) from export zips."""

    name = "facebook_posts"
    source_kind = "facebook-posts"
    file_kind = "zip"
    schema_type = "SocialMediaPosting"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        with zipfile.ZipFile(source_path) as zf:
            for fi, name in enumerate(sorted(zf.namelist())):
                if not name.startswith(_POSTS_PATTERN) or not name.endswith(".html"):
                    continue
                filename = Path(name).name
                bucket = _POSTS_THREAD_BUCKETS.get(filename, "Other Posts")

                html = zf.read(name).decode("utf-8", errors="replace")
                for pi, post in enumerate(_parse_post_html(html)):
                    body = str(post.get("body_text") or post.get("subject") or "[empty post]")
                    subject = post.get("subject")
                    date_sent = post.get("date_sent")
                    has_att = bool(post.get("has_attachment"))

                    dedup_seed = f"facebook-posts|{name}|{pi}|{date_sent}|{body[:100]}"
                    raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                    yield AdapterRow(
                        schema_type="SocialMediaPosting",
                        rfc822_message_id=f"facebook-posts:{raw_hash}",
                        subject=str(subject) if subject else None,
                        sender_address="self",
                        sender_name="self",
                        direction="outbound",
                        date_sent=str(date_sent) if date_sent else None,
                        body_text=body,
                        body_text_source="facebook-html",
                        has_attachments=1 if has_att else 0,
                        attachment_count=1 if has_att else 0,
                        source_byte_offset=fi,
                        source_byte_length=pi,
                        raw_hash=raw_hash,
                        body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                        thread_key=f"facebook-posts:{bucket}",
                    )
