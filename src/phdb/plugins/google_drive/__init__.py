"""phdb.plugins.google_drive — Google Drive Takeout ZIP ingester.

Phase 7 brief 027 port of the phdb Plugin Architecture plan. Ingests
documents from Google Takeout ZIPs (or extracted directories) under
``Drive/``: per-file body text extraction via the shared
``phdb.formats.document_extract.EXTRACTORS`` dispatch (PDF, DOCX,
XLSX, IPYNB, HTML, CSV, JSON, TXT, MD, RTF).

Replaces the legacy ``phdb.adapters.google_drive`` module deleted in
the same commit per Phase 0 Q14 (no shim). Writes to the existing
``documents`` typed table (migration 0008); no schema changes.
"""

from __future__ import annotations

from phdb.plugins.google_drive.plugin import GoogleDrivePlugin

__all__ = ["GoogleDrivePlugin"]
