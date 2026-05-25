"""phdb.plugins.file_revisions — git-for-ideas capture walker.

Walks a git repo's commit log; emits one ``file_revisions`` row per
(commit_sha, markdown file changed) and computes ``revision_triple_deltas``
from the difference between parent and new frontmatter+wikilink graphs.

Scope is markdown files only, excluding ``.obsidian/``, ``attachments/``,
``.claude/``, ``.git/``. Bodies are not stored — the row references
``git_blob_sha`` for ``git cat-file -p`` materialization.

See ``Outputs/Plans/Git for Ideas.md`` and the accompanying
``DECISIONS.md`` for the locked design.
"""

from __future__ import annotations

from phdb.plugins.file_revisions.plugin import FileRevisionsPlugin

__all__ = ["FileRevisionsPlugin"]
