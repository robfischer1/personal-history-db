"""Amazon zip format parser — yields AmazonRecord intermediates.

Source: Amazon's "Request Your Data" zip with 8 data streams (CSVs + JSON).
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from phdb.records.provenance import Provenance

_MAX_BODY_LEN = 5000

HANDLERS: list[tuple[str, str, str, str]] = [
    ("Your Amazon Orders/Order History.csv", "Orders", "OrderAction", "csv_order"),
    ("Your Amazon Orders/Cart History.csv", "Cart", "Action", "csv_cart"),
    ("Your Amazon Orders/Digital Content Orders.csv", "Digital Orders", "OrderAction", "csv_order"),
    (
        "Additional Data/Amazon.Lists.Wishlist.1.1/Amazon.Lists.Wishlist.json",
        "Wishlist",
        "Product",
        "json_wishlist",
    ),
    (
        "Additional Data/Retail.CustomerReviews/datasets/"
        "Retail.CustomerReviews.ReviewsVersions1/"
        "Retail.CustomerReviews.ReviewsVersions1.csv",
        "Reviews",
        "Review",
        "csv_review",
    ),
    (
        "Additional Data/Audible.AudibleLibraryItemFactoryService/datasets/Library/Library.csv",
        "Audible Library",
        "Book",
        "csv_audible",
    ),
    (
        "Additional Data/PrimeVideo.WatchEvent.1/PrimeVideo.WatchEvent.1.csv",
        "Prime Video Watch",
        "WatchAction",
        "csv_video",
    ),
    (
        "Additional Data/Kindle.UnifiedLibraryIndex/datasets/"
        "Kindle.UnifiedLibraryIndex.CustomerOrders/"
        "Kindle.UnifiedLibraryIndex.CustomerOrders.csv",
        "Kindle Orders",
        "OrderAction",
        "csv_kindle_order",
    ),
]


@dataclass(frozen=True)
class AmazonRecord:
    """Intermediate record from an Amazon data export stream."""

    provenance: Provenance
    schema_type: str
    stream: str
    subject: str
    body_text: str
    date_sent: str | None
    sender_name: str


def _make_record(
    schema_t: str,
    stream: str,
    subject: str,
    body: str,
    ts: str | None,
    sender_name: str,
    file_idx: int,
    row_idx: int | str,
    dedup_key: str,
    source_path: str,
) -> AmazonRecord:
    body = body[:_MAX_BODY_LEN]
    raw_hash = hashlib.sha256(f"amazon|{file_idx}|{row_idx}|{dedup_key}".encode()).hexdigest()
    return AmazonRecord(
        provenance=Provenance(
            source_path=source_path,
            raw_hash=raw_hash,
            source_byte_offset=file_idx,
            source_byte_length=int(row_idx) if isinstance(row_idx, int) else 0,
        ),
        schema_type=schema_t,
        stream=stream,
        subject=subject[:200],
        body_text=body,
        date_sent=ts or None,
        sender_name=sender_name,
    )


def _iter_csv(
    data_bytes: bytes, kind: str, stream: str, schema_t: str, fi: int, source_path: str
) -> Iterator[AmazonRecord]:
    text = data_bytes.decode("utf-8-sig", errors="replace")
    rdr = csv.DictReader(io.StringIO(text))
    for ri, row in enumerate(rdr):
        if kind == "csv_order":
            asin = row.get("ASIN", "")
            title = row.get("Product Name", "") or row.get("Title", "") or asin
            order_id = row.get("Order ID", "") or row.get("Carrier Name & Tracking Number", "")
            order_date = row.get("Order Date", "") or row.get("Shipment Date", "")
            qty = row.get("Quantity", "") or row.get("Affected Item Quantity", "")
            price = row.get("Item Subtotal", "") or row.get("Total Charged", "")
            subject = f"{stream}: {title}"
            body = f"Title: {title}\nASIN: {asin}\nOrder: {order_id}\nDate: {order_date}\nQty: {qty}\nPrice: {price}"
            yield _make_record(schema_t, stream, subject, body, order_date or None, stream, fi, ri, f"{order_id}|{asin}", source_path)
        elif kind == "csv_cart":
            asin = row.get("ASIN", "")
            ts = row.get("Date Added to Cart", "")
            body = f"ASIN: {asin}\nList: {row.get('Cart List', '')}\nAdded: {ts}"
            yield _make_record(schema_t, stream, f"Cart: {asin}", body, ts or None, stream, fi, ri, f"{asin}|{ts}", source_path)
        elif kind == "csv_review":
            asin = row.get("ASIN", "")
            product = row.get("ProductName", "")
            rating = row.get("StarRating", "")
            ts = row.get("SubmissionDate", "") or row.get("LastModifiedDate", "")
            review_title = row.get("Title", "") or row.get("ReviewTitle", "")
            body_txt = row.get("Body", "") or row.get("ReviewBody", "")
            subject = f"Review: {product} ({rating}★)"
            body = f"Product: {product}\nRating: {rating}\nTitle: {review_title}\n\n{body_txt}"
            yield _make_record(schema_t, stream, subject, body, ts or None, product, fi, ri, f"{asin}|{ts}", source_path)
        elif kind == "csv_audible":
            title = row.get("title_in_english", "")
            length = row.get("length_in_minutes", "")
            avail = row.get("available_in_library", "")
            body = f"Title: {title}\nLength: {length} min\nAvailable: {avail}"
            yield _make_record(schema_t, stream, f"Audible: {title}", body, None, title, fi, ri, title, source_path)
        elif kind == "csv_video":
            title = row.get("TitleName", "")
            ts = row.get("LatestWatchProgress", "") or row.get("MostRecentWatchDate", "")
            secs = row.get("SecondsWatched", "")
            desc = row.get("TitleDescription", "")
            body = f"Title: {title}\nWatched at: {ts}\nSecondsWatched: {secs}\n\n{desc}"
            yield _make_record(schema_t, stream, f"PrimeVideo: {title}", body, ts or None, title, fi, ri, f"{title}|{ts}", source_path)
        elif kind == "csv_kindle_order":
            title = row.get("Product Name", "")
            asin = row.get("ASIN", "")
            order_id = row.get("Order ID", "")
            otype = row.get("Order Type", "")
            body = f"Title: {title}\nASIN: {asin}\nOrder: {order_id}\nType: {otype}"
            yield _make_record(schema_t, stream, f"Kindle {otype}: {title}", body, None, title, fi, ri, f"{order_id}|{asin}", source_path)


def _iter_wishlist_json(
    data_bytes: bytes, stream: str, schema_t: str, fi: int, source_path: str
) -> Iterator[AmazonRecord]:
    try:
        data = json.loads(data_bytes)
    except json.JSONDecodeError:
        return
    if not isinstance(data, list):
        return
    for li, list_obj in enumerate(data):
        if not isinstance(list_obj, dict):
            continue
        for list_name, items in list_obj.items():
            if not isinstance(items, list):
                continue
            for ii, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                title = item.get("itemTitle", "") or item.get("title", "") or "Untitled"
                asin = item.get("itemAsin", "") or item.get("asin", "")
                added = item.get("addedDate", "") or item.get("dateAdded", "")
                priority = item.get("priority", "")
                price = item.get("itemPrice", "")
                subject = f"Wishlist [{list_name}]: {title}"
                body = f"List: {list_name}\nTitle: {title}\nASIN: {asin}\nAdded: {added}\nPriority: {priority}\nPrice: {price}"
                yield _make_record(
                    schema_t, stream, subject, body, added or None, title,
                    fi, f"{li}-{ii}", f"{list_name}|{asin}|{title}", source_path,
                )


def parse(source_path: Path) -> Iterator[AmazonRecord]:
    """Parse an Amazon Data Export zip, yielding AmazonRecord intermediates."""
    source_str = str(source_path)
    with zipfile.ZipFile(source_path) as zf:
        for fi, (path, stream, schema_t, kind) in enumerate(HANDLERS):
            try:
                data_bytes = zf.read(path)
            except KeyError:
                continue
            if kind == "json_wishlist":
                yield from _iter_wishlist_json(data_bytes, stream, schema_t, fi, source_str)
            else:
                yield from _iter_csv(data_bytes, kind, stream, schema_t, fi, source_str)
