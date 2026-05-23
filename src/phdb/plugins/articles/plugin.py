"""ArticlesPlugin — Phase 7 brief 024 port of the vault articles ingester.

Source: a directory of ``Resources/Articles/`` ``.md`` files with YAML
frontmatter. Each file whose frontmatter declares
``note_type: source-material`` becomes one row in the ``articles``
typed table. The folder note (``note_type: Folder``) is skipped by the
``phdb.formats.articles_md`` parser. Frontmatter is parsed into typed
columns; the body is stored verbatim for faithful round-trip
materialization.

Built originally for the Articles Dissolution Pilot (closed
2026-05-19); ported under Phase 7 of the phdb Plugin Architecture
plan. Replaces the legacy ``phdb.adapters.articles`` module deleted in
the same commit per Phase 0 Q14 (no shim). Reuses the ``articles``
typed table introduced in migration 0013; no schema changes.

The ``articles`` table SQL is lifted from
``phdb.adapters.base._INSERT_ARTICLE_SQL`` so the plugin can run
without inheriting the deprecated ``Adapter`` base class.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.articles_md import ArticleRecord
from phdb.formats.articles_md import parse as parse_articles_md
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.articles")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "vault-articles",
    file_kind: str = "md",
) -> int:
    """Insert (or refresh) a source_files row for the given path.

    Mirrors the helper used by raindrop / spotify / goodreads /
    apple_notes_full plugin ports — Phase 7 will lift this into a
    shared ``phdb.core.sources`` helper as more plugins port.
    """
    cur = conn.execute(
        """INSERT INTO source_files
           (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
           VALUES (?, ?, ?, ?, NULL,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           ON CONFLICT(source_path) DO UPDATE
             SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           RETURNING id""",
        (str(source_path), None, file_kind, source_kind),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


_INSERT_ARTICLE_SQL = """\
INSERT OR IGNORE INTO articles (
    schema_type, subject, url, publisher, creator, description, image_url,
    categories, tags, aliases, note_type, author_type,
    file_path, file_size, ctime, mtime,
    body_text, body_text_source, body_text_hash,
    raw_hash, bucket, source_file_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


class ArticlesPlugin(PhdbSourcePlugin):
    """Vault Resources/Articles/ markdown plugin — Phase 7 port."""

    SOURCE_KIND = "vault-articles"
    FILE_KIND = "md"
    TARGET_TABLE = "articles"
    SCHEMA_TYPE = "Article"
    BATCH_SIZE = 100

    def __init__(
        self,
        manifest: PluginManifest | None = None,
    ) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for the articles root.

        The ``articles_md`` parser walks the directory itself and filters
        on ``note_type: source-material`` — discovery here yields the
        directory once so ``parse()`` can hand it to the parser. A single
        file path is also accepted (treated as the root).
        """
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        if root.is_dir():
            yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ArticleRecord]:
        """Yield ArticleRecord intermediates from a Resources/Articles/ root.

        Delegates to ``phdb.formats.articles_md.parse`` — the directory
        walk + frontmatter filtering live there.
        """
        yield from parse_articles_md(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ArticleRecord,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Insert one ArticleRecord into the ``articles`` typed table.

        Returns the inserted row id, or ``None`` when the row was a
        dedup-skip (``(source_file_id, raw_hash)`` already present —
        idempotent re-runs).
        """
        sf_id = source_file_id if source_file_id is not None else 0

        body_text = record.body_text or ""
        body_text_hash = (
            hashlib.sha256(body_text.encode("utf-8")).hexdigest()
            if body_text
            else None
        )

        cur = conn.execute(
            _INSERT_ARTICLE_SQL,
            (
                self.SCHEMA_TYPE,                # schema_type
                record.title,                    # subject
                record.url,                      # url
                record.publisher,                # publisher
                record.creator,                  # creator
                record.description,              # description
                record.image_url,                # image_url
                record.categories,               # categories (JSON array)
                record.tags,                     # tags (JSON array)
                record.aliases,                  # aliases (JSON array)
                record.note_type,                # note_type
                record.author_type,              # author_type
                record.file_path,                # file_path (relative)
                record.file_size,                # file_size
                record.ctime,                    # ctime
                record.mtime,                    # mtime
                body_text,                       # body_text
                record.body_text_source,         # body_text_source
                body_text_hash,                  # body_text_hash
                record.provenance.raw_hash,      # raw_hash
                record.bucket,                   # bucket
                sf_id,                           # source_file_id
            ),
        )
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest articles <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No articles-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Resources/Articles/ directory.

        Mirrors the legacy ``ArticlesAdapter.run`` surface (inherited
        from ``Adapter.run``) — the ported tests consume this entry
        point. ``rows_inserted`` / ``rows_skipped`` count individual
        article rows; dedup-skips (idempotent re-runs) increment
        ``rows_skipped``.
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
            row_id = self.ingest_row(
                conn, record, source_file_id=source_file_id,
            )
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
            "[articles] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
