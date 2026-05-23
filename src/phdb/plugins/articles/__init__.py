"""phdb.plugins.articles — Vault Resources/Articles/ markdown ingester.

Phase 7 brief 024 port of the phdb Plugin Architecture plan. Reads
``Resources/Articles/`` markdown notes (saved web articles) with
``note_type: source-material`` frontmatter and writes one row per file
into the ``articles`` typed table (migration 0013); no schema changes.

Replaces the legacy ``phdb.adapters.articles`` module deleted in the
same commit per Phase 0 Q14 (no shim). Consumes ``ArticleRecord``
intermediates from ``phdb.formats.articles_md``.
"""

from __future__ import annotations

from phdb.plugins.articles.plugin import ArticlesPlugin

__all__ = ["ArticlesPlugin"]
