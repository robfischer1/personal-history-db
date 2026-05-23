"""Persistent pending-review queue for facet coalescence proposals.

Phase 8C of the phdb Plugin Architecture plan. ``PeopleFacetPlugin``
and ``PlacesFacetPlugin`` buffer low-confidence merge proposals in
``self.pending_review`` — an in-memory list that vanishes when the
process exits. The Phase 8C review CLI needs that list to survive
across invocations.

Storage choice (Phase 8C Q2): JSONL file per facet at
``<instance_dir>/facet_coalescence_pending/<facet>.jsonl``.

Reasons for JSONL over a DB table:

- Instance-side state shouldn't bleed into the DB (storage-tier rule).
- JSONL is easy for Rob to inspect / hand-edit / regenerate.
- The pending queue is transient — re-running coalescence rebuilds it.
- Append-only writes give crash safety without transaction ceremony.

API:

- ``load_pending(facet_type, instance_dir)`` — read all serialized
  proposals from disk, dedupe by emission signature, return as
  ``list[MergeProposal]``. Returns ``[]`` if file is missing.
- ``save_pending(facet_type, instance_dir, proposals)`` — replace the
  file contents with the given proposals (atomic via temp file).
- ``append_pending(facet_type, instance_dir, proposal)`` — append one
  proposal to the JSONL. Used by ``coalesce()`` to persist as it goes.

The ``facet_type`` argument is the short facet name (``"people"``,
``"places"``) — matches the facet plugin's manifest ``name``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from phdb.facets._coalescence_lib import MergeProposal

QUEUE_DIRNAME = "facet_coalescence_pending"


def _queue_dir(instance_dir: Path) -> Path:
    """Resolve the queue directory under ``instance_dir`` (create if absent)."""
    d = Path(instance_dir) / QUEUE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _queue_path(facet_type: str, instance_dir: Path) -> Path:
    """Per-facet JSONL path: ``<instance_dir>/facet_coalescence_pending/<facet>.jsonl``."""
    return _queue_dir(instance_dir) / f"{facet_type}.jsonl"


def _serialize_emission(emission: Any) -> dict[str, Any]:
    """Round-trip an emission (``FacetEmission`` or dict) to JSON-friendly dict."""
    payload = getattr(emission, "payload", None)
    if isinstance(payload, dict):
        return {
            "source_table": getattr(emission, "source_table", None),
            "source_id": getattr(emission, "source_id", None),
            "facet_type": getattr(emission, "facet_type", None),
            "payload": dict(payload),
        }
    if isinstance(emission, dict):
        return dict(emission)
    # Last resort — try to coerce.
    return {"repr": repr(emission)}


def _deserialize_emission(data: dict[str, Any]) -> Any:
    """Reverse of ``_serialize_emission`` — returns a ``FacetEmission`` when possible."""
    # Lazy import to avoid cycle at module import.
    from phdb.core.plugin.bus import FacetEmission

    if {"source_table", "source_id", "facet_type", "payload"} <= set(data.keys()):
        return FacetEmission(
            source_table=data["source_table"],
            source_id=data["source_id"],
            facet_type=data["facet_type"],
            payload=data["payload"] or {},
        )
    return data


def _serialize_proposal(proposal: MergeProposal) -> dict[str, Any]:
    """Encode a MergeProposal as a JSON-friendly dict."""
    return {
        "into_node_id": proposal.into_node_id,
        "rule": proposal.rule,
        "confidence": proposal.confidence,
        "payload": dict(proposal.payload or {}),
        "from_emissions": [_serialize_emission(e) for e in proposal.from_emissions],
    }


def _deserialize_proposal(data: dict[str, Any]) -> MergeProposal:
    """Decode a JSONL line back into a MergeProposal."""
    return MergeProposal(
        into_node_id=int(data.get("into_node_id", -1)),
        from_emissions=[_deserialize_emission(e) for e in data.get("from_emissions", [])],
        rule=str(data.get("rule", "")),
        confidence=float(data.get("confidence", 0.0)),
        payload=dict(data.get("payload", {}) or {}),
    )


def _signature(proposal: MergeProposal) -> tuple[Any, ...]:
    """Dedupe key — survivor id + tuple of (source_table, source_id) for each emission."""
    sig: list[Any] = [proposal.into_node_id, proposal.rule]
    for e in proposal.from_emissions:
        st = getattr(e, "source_table", None)
        sid = getattr(e, "source_id", None)
        if st is None and isinstance(e, dict):
            st = e.get("source_table")
            sid = e.get("source_id")
        sig.append((st, sid))
    return tuple(sig)


def load_pending(
    facet_type: str, instance_dir: Path,
) -> list[MergeProposal]:
    """Read the per-facet JSONL queue; dedupe by signature; return proposals.

    Returns an empty list if the file is missing or empty. Malformed
    JSONL lines are skipped silently (the queue is meant to be
    self-healing — a corrupt line shouldn't block review).
    """
    path = _queue_path(facet_type, instance_dir)
    if not path.is_file():
        return []
    proposals: list[MergeProposal] = []
    seen: set[tuple[Any, ...]] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                proposal = _deserialize_proposal(data)
            except Exception:
                continue
            sig = _signature(proposal)
            if sig in seen:
                continue
            seen.add(sig)
            proposals.append(proposal)
    return proposals


def save_pending(
    facet_type: str, instance_dir: Path,
    proposals: list[MergeProposal],
) -> None:
    """Replace the queue file with the given proposals (atomic write).

    Writes to a temp file in the same directory then renames over the
    target — keeps the queue intact if the process dies mid-write.
    """
    path = _queue_path(facet_type, instance_dir)
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{facet_type}.", suffix=".jsonl.tmp", dir=parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for proposal in proposals:
                f.write(json.dumps(_serialize_proposal(proposal), default=str))
                f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_pending(
    facet_type: str, instance_dir: Path, proposal: MergeProposal,
) -> None:
    """Append one proposal to the JSONL queue (no dedup at write time)."""
    path = _queue_path(facet_type, instance_dir)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(_serialize_proposal(proposal), default=str))
        f.write("\n")


__all__ = [
    "QUEUE_DIRNAME",
    "append_pending",
    "load_pending",
    "save_pending",
]
