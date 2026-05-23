"""phdb.plugins.onedrive — OneDrive local sync directory ingester.

Phase 7 brief 028 port of the phdb Plugin Architecture plan. Ingests
documents from a local OneDrive root directory (F:\\OneDrive\\ post-
2026-05-13 reorg). Walks the ``Outputs/``, ``Reference/`` and
``Records/`` top-level pillars; extracts body text via the shared
``phdb.formats.document_extract.EXTRACTORS`` dispatch (PDF, DOCX,
XLSX, IPYNB, HTML, CSV, JSON, TXT, MD, RTF).

Reference/ has a body-extract allowlist: active-pursuit subdirs get
full body extraction; everything else yields metadata-only rows
(``body_text=None``, ``is_bulk=1``) per the OneDrive Reference/
allowlist policy. The list is intentionally narrow — do not widen
it without a propagation pass.

Replaces the legacy ``phdb.adapters.onedrive`` module deleted in the
same commit per Phase 0 Q14 (no shim). Writes to the existing
``documents`` typed table (migration 0008); no schema changes.
"""

from __future__ import annotations

from phdb.plugins.onedrive.plugin import OneDrivePlugin

__all__ = ["OneDrivePlugin"]
