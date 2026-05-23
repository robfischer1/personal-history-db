"""phdb.plugins.staged_md — Generic staging-markdown ingester.

Phase 7 brief 026 port of the phdb Plugin Architecture plan. Source:
directories of ``.md`` files with YAML frontmatter — each file becomes
one ``documents`` row whose ``schema_type`` column carries the
frontmatter ``@type`` value (filtered against the parser's allowlist).

Replaces the legacy ``phdb.adapters.staged_md`` module deleted in the
same commit per Phase 0 Q14 (no shim). Reuses the ``documents`` typed
table (migration 0008); no schema changes.
"""

from __future__ import annotations

from phdb.plugins.staged_md.plugin import StagedMdPlugin

__all__ = ["StagedMdPlugin"]
