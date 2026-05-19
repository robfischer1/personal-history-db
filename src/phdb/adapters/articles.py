"""Articles adapter — ingests vault Resources/Articles/ .md files into the articles table.

Consumes ArticleRecord records from phdb.formats.articles_md.
Source: the Resources/Articles/ vault directory. Each .md file with
note_type: source-material becomes one `articles` row; the folder note
(note_type: Folder) is skipped. Frontmatter is parsed into typed columns;
the body is stored verbatim for faithful round-trip materialization.

Built for the Articles Dissolution Pilot (Outputs/Plans/Articles Dissolution Pilot.md).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.articles_md import ArticleRecord, parse as parse_articles_md
from phdb.log import get_logger

log = get_logger("phdb.adapters.articles")

# Re-export for backward compatibility
__all__ = ["ArticlesAdapter", "ArticleRecord"]


class ArticlesAdapter(Adapter):
    """Ingest vault Resources/Articles/ files into the `articles` typed table."""

    name = "articles"
    source_kind = "vault-articles"
    file_kind = "md"
    schema_type = "Article"
    target_table = "articles"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 100

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse_articles_md(source_path):
            extra: dict[str, object] = {
                "url": rec.url,
                "publisher": rec.publisher,
                "creator": rec.creator,
                "description": rec.description,
                "image_url": rec.image_url,
                "categories": rec.categories,
                "tags": rec.tags,
                "aliases": rec.aliases,
                "note_type": rec.note_type,
                "author_type": rec.author_type,
                "mtime": rec.mtime,
            }

            yield AdapterRow(
                schema_type="Article",
                subject=rec.title,
                body_text=rec.body_text,
                body_text_source=rec.body_text_source,
                raw_hash=rec.provenance.raw_hash,
                file_path=rec.file_path,
                file_size=rec.file_size,
                ctime=rec.ctime,
                bucket=rec.bucket,
                extra=extra,
            )
