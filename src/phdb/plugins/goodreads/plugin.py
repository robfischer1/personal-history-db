"""GoodreadsPlugin — Phase 7 port of the Goodreads CSV ingester.

Consumes a Goodreads library export CSV and emits to two typed tables:

- ``books`` (@type ``Book``) — one row per item with a non-empty title.
- ``reviews`` (@type ``Review``) — one row per item that carries a
  ``rating`` or ``review_text``. The current ``goodreads_csv`` parser
  does not populate either field from the export CSV, so the Review
  emission path is dormant for the standard library export; it exists
  to satisfy the manifest's multi-emit declaration and to receive
  real data once the parser starts pulling rating/review columns from
  richer exports.

The pre-port adapter lived at ``phdb.adapters.goodreads`` and was
deleted in the same commit per Phase 0 Q14 (no shim). The legacy
adapter only wrote to ``books``; the new plugin preserves that exact
behavior on the existing fixture (4 books, 0 reviews) while wiring up
the Review path for future ingests.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.goodreads_csv import parse as parse_goodreads_csv
from phdb.log import get_logger
from phdb.records import ConsumedItem
from phdb.triples import get_predicate, resolve_node

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.goodreads")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
_INSERT_BOOK_SQL = """\
INSERT OR IGNORE INTO books (
    schema_type, name, isbn, publisher, date_published,
    raw_hash, source_file_id
) VALUES (
    'Book', ?, ?, ?, ?,
    ?, ?
)"""


_INSERT_REVIEW_SQL = """\
INSERT OR IGNORE INTO reviews (
    schema_type, review_key, subject, sender_address, sender_name,
    direction, date_reviewed, body_text, body_text_source, body_text_hash,
    is_bulk, bulk_signal, raw_hash, source_file_id
) VALUES (
    'Review', ?, ?, ?, ?,
    'self', ?, ?, 'goodreads-csv', ?,
    0, NULL, ?, ?
)"""


def _emit_book_thread_triple(
    conn: sqlite3.Connection,
    table: str,
    row_id: int,
    thread_key: str,
    source_kind: str,
) -> None:
    """Emit an inThread triple linking a book/review row into a thread node.

    Mirrors the legacy ``Adapter._link_message_thread`` behavior so the
    ported plugin keeps producing the ``inThread`` bridges the test
    suite asserts on.
    """
    pred = get_predicate(conn, "inThread")
    if not pred:
        return
    in_thread_id = pred["id"]

    record_label = f"{table}:{row_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table=table, source_id=row_id,
    )

    thread_label = thread_key if ":" in thread_key else f"{source_kind}:{thread_key}"
    thread_node_id = resolve_node(conn, thread_label, "thread")

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'plugin', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )


class GoodreadsPlugin(PhdbSourcePlugin):
    """Goodreads CSV library plugin — emits Books + Reviews."""

    SOURCE_KIND = "goodreads"
    FILE_KIND = "csv"
    BATCH_SIZE = 500
    THREAD_KEY = "goodreads:library"

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Goodreads CSV."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.csv")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ConsumedItem]:
        """Yield ConsumedItem records from one Goodreads CSV."""
        yield from parse_goodreads_csv(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ConsumedItem,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Persist a ConsumedItem to ``books`` and (optionally) ``reviews``.

        Returns the ``books`` row id when a new book was inserted,
        ``None`` when the row was a duplicate and no insert took place.
        The Review emission is a best-effort side-channel — it does not
        change the return value (which the convenience runner uses to
        compute rows_inserted vs rows_skipped, matching the legacy
        Book-only accounting).
        """
        sf_id = source_file_id if source_file_id is not None else 0
        title = record.title

        cur = conn.execute(
            _INSERT_BOOK_SQL,
            (
                title,                          # name
                record.isbn,                    # isbn
                record.author,                  # publisher (CSV publisher → ConsumedItem.author)
                None,                           # date_published
                record.provenance.raw_hash,     # raw_hash
                sf_id,                          # source_file_id
            ),
        )
        book_id = int(cur.lastrowid) if cur.rowcount else None  # type: ignore[arg-type]

        if book_id is not None:
            _emit_book_thread_triple(
                conn, "books", book_id, self.THREAD_KEY, self.SOURCE_KIND,
            )

            # Review path — only fires when the record carries review
            # content. The standard library-export parser leaves both
            # fields None, so this branch is dormant on the current
            # fixture; richer exports will exercise it.
            if record.rating is not None or record.review_text:
                review_body = record.review_text or ""
                review_hash = hashlib.sha256(
                    review_body.encode("utf-8")
                ).hexdigest()
                review_raw_hash = hashlib.sha256(
                    f"{record.provenance.raw_hash}|review".encode()
                ).hexdigest()
                rcur = conn.execute(
                    _INSERT_REVIEW_SQL,
                    (
                        f"goodreads:review:{record.provenance.raw_hash}",  # review_key
                        title,                                              # subject
                        record.isbn,                                        # sender_address
                        record.author,                                      # sender_name
                        record.date_consumed,                               # date_reviewed
                        review_body,                                        # body_text
                        review_hash,                                        # body_text_hash
                        review_raw_hash,                                    # raw_hash
                        sf_id,                                              # source_file_id
                    ),
                )
                if rcur.rowcount:
                    review_id = int(rcur.lastrowid)  # type: ignore[arg-type]
                    _emit_book_thread_triple(
                        conn, "reviews", review_id, self.THREAD_KEY, self.SOURCE_KIND,
                    )

        return book_id

    def register_cli(self, parser: Any) -> None:
        """Goodreads has no plugin-specific CLI subcommands yet."""
        return None

    def register_tools(self, server: Any) -> None:
        """Goodreads has no plugin-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Goodreads CSV.

        Mirrors the legacy ``Adapter.run`` surface — the ported tests
        consume this entry point. The summary's rows_inserted /
        rows_skipped counts track the ``books`` table only (matching
        legacy semantics); review rows ride along when present and are
        not reflected in those counters.
        """
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(conn, record, source_file_id=source_file_id)
            if row_id is None:
                report.rows_skipped += 1
            else:
                report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[goodreads] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
