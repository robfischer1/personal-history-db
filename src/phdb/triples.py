"""Triple store service — typed-edge graph over the vault and corpus.

Wikidata-inspired RDF-triple store: nodes (addressable referents),
predicates (controlled edge-type vocabulary), triples (subject → predicate
→ object with timestamps and provenance), and qualifiers (reified
key/value metadata on triples).

All public functions take ``conn: sqlite3.Connection`` as first arg —
the module is stateless; callers own connection lifecycle.

Tables created by migration 0012_predicate_table.sql.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from phdb.log import get_logger

log = get_logger("phdb.triples")

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _normalize(label: str) -> str:
    return label.strip().lower()


# === Node resolution ===


def resolve_node(
    conn: sqlite3.Connection,
    label: str,
    kind: str = "concept",
    *,
    vault_path: str | None = None,
    source_table: str | None = None,
    source_id: int | None = None,
    create: bool = True,
) -> int | None:
    """Find or create a node by (kind, normalized_label).

    Returns the node ID, or None if ``create=False`` and no match exists.
    When creating, ``vault_path`` / ``source_table`` / ``source_id`` are
    set on the new row. When finding an existing row, missing corpus
    linkage columns are backfilled if the caller supplies them.
    """
    normalized = _normalize(label)

    row = conn.execute(
        "SELECT id, source_table, source_id FROM nodes"
        " WHERE kind = ? AND normalized_label = ?",
        (kind, normalized),
    ).fetchone()

    if row is not None:
        node_id: int = row[0]
        if source_table and source_id and not row[1]:
            conn.execute(
                "UPDATE nodes SET source_table = ?, source_id = ? WHERE id = ?",
                (source_table, source_id, node_id),
            )
        return node_id

    if not create:
        return None

    cur = conn.execute(
        "INSERT INTO nodes (label, normalized_label, kind, vault_path,"
        " source_table, source_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (label.strip(), normalized, kind, vault_path, source_table, source_id),
    )
    return cur.lastrowid


def resolve_node_for_wikilink(
    conn: sqlite3.Connection,
    wikilink: str,
    *,
    create: bool = True,
) -> int | None:
    """Resolve a ``[[WikiLink]]`` or ``[[WikiLink|alias]]`` to a node ID.

    Strips brackets and alias; uses kind='file' if label matches a known
    vault path, else kind='concept'.
    """
    m = _WIKILINK_RE.match(wikilink) if wikilink.startswith("[[") else None
    label = m.group(1) if m else wikilink.strip("[] ")

    existing = conn.execute(
        "SELECT id FROM nodes WHERE kind = 'file' AND normalized_label = ?",
        (_normalize(label),),
    ).fetchone()
    if existing:
        return int(existing[0])

    return resolve_node(conn, label, kind="concept", create=create)


# === Predicate lookup ===


def get_predicate(
    conn: sqlite3.Connection,
    name: str,
) -> dict[str, Any] | None:
    """Look up a predicate by name. Returns dict or None."""
    row = conn.execute(
        "SELECT id, name, inverse_predicate_id, symmetric, description"
        " FROM predicates WHERE name = ?",
        (name,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "inverse_predicate_id": row[2],
        "symmetric": bool(row[3]),
        "description": row[4],
    }


def list_predicates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all predicates as dicts."""
    rows = conn.execute(
        "SELECT id, name, inverse_predicate_id, symmetric, description"
        " FROM predicates ORDER BY id"
    ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "inverse_predicate_id": r[2],
            "symmetric": bool(r[3]),
            "description": r[4],
        }
        for r in rows
    ]


# === Triple CRUD ===


def add_triple(
    conn: sqlite3.Connection,
    subject: str | int,
    predicate: str | int,
    object_: str | int | None = None,
    *,
    observed_at: str | None = None,
    provenance: str = "explicit",
    source_ref: str | None = None,
    subject_kind: str = "concept",
    object_kind: str = "concept",
    qualifiers: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Add a triple (idempotent via INSERT OR IGNORE).

    ``subject`` / ``predicate`` / ``object_`` can be IDs (int) or labels
    (str). String subjects/objects are resolved to nodes; string
    predicates are looked up by name.

    Returns ``{"triple_id": int, "created": bool, ...}``.
    """
    if isinstance(predicate, str):
        pred = get_predicate(conn, predicate)
        if pred is None:
            raise ValueError(f"Unknown predicate: {predicate!r}")
        predicate_id: int = pred["id"]
    else:
        predicate_id = predicate

    if isinstance(subject, str):
        subj_id = resolve_node(conn, subject, kind=subject_kind)
        assert subj_id is not None
    else:
        subj_id = subject

    obj_id: int | None = None
    if object_ is not None:
        if isinstance(object_, str):
            obj_id = resolve_node(conn, object_, kind=object_kind)
        else:
            obj_id = object_

    cur = conn.execute(
        "INSERT OR IGNORE INTO triples"
        " (subject_node_id, predicate_id, object_node_id,"
        "  observed_at, provenance, source_ref)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (subj_id, predicate_id, obj_id, observed_at, provenance, source_ref),
    )
    created = cur.rowcount > 0

    if created:
        triple_id = cur.lastrowid
    else:
        row = conn.execute(
            "SELECT id FROM triples"
            " WHERE subject_node_id = ? AND predicate_id = ?"
            "   AND COALESCE(object_node_id, -1) = ?"
            "   AND provenance = ?"
            "   AND COALESCE(source_ref, '') = ?",
            (
                subj_id,
                predicate_id,
                obj_id if obj_id is not None else -1,
                provenance,
                source_ref or "",
            ),
        ).fetchone()
        triple_id = row[0] if row else None

    if created and qualifiers:
        _attach_qualifiers(conn, triple_id, qualifiers)

    conn.commit()

    return {
        "triple_id": triple_id,
        "created": created,
        "subject_node_id": subj_id,
        "predicate_id": predicate_id,
        "object_node_id": obj_id,
    }


def _attach_qualifiers(
    conn: sqlite3.Connection,
    triple_id: int | None,
    qualifiers: list[dict[str, str]],
) -> None:
    """Insert qualifier rows for a triple."""
    if triple_id is None:
        return
    conn.executemany(
        "INSERT INTO qualifiers (triple_id, key, value) VALUES (?, ?, ?)",
        [(triple_id, q["key"], q.get("value")) for q in qualifiers],
    )


def add_qualifier(
    conn: sqlite3.Connection,
    triple_id: int,
    key: str,
    value: str | None = None,
) -> int:
    """Add a single qualifier to an existing triple. Returns qualifier ID."""
    cur = conn.execute(
        "INSERT INTO qualifiers (triple_id, key, value) VALUES (?, ?, ?)",
        (triple_id, key, value),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# === Query ===


def query_triples(
    conn: sqlite3.Connection,
    *,
    subject: str | int | None = None,
    predicate: str | int | None = None,
    object_: str | int | None = None,
    provenance: str | None = None,
    since: str | None = None,
    until: str | None = None,
    include_null_objects: bool | None = None,
    include_inverse: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query triples with optional filters.

    String arguments for ``subject`` / ``predicate`` / ``object_`` are
    resolved to IDs. Set ``include_null_objects=True`` to return only
    open triples, ``False`` to exclude them, or ``None`` (default) for
    all.

    When ``include_inverse=True`` and a predicate has an inverse, the
    query also returns triples where subject/object are swapped through
    the inverse predicate.
    """
    clauses: list[str] = []
    params: list[Any] = []

    subj_id = _resolve_filter_id(conn, subject, "node")
    pred_id = _resolve_filter_id(conn, predicate, "predicate")
    obj_id = _resolve_filter_id(conn, object_, "node")

    if include_inverse and pred_id is not None:
        return _query_with_inverse(
            conn, subj_id, pred_id, obj_id, provenance,
            since, until, include_null_objects, limit, offset,
        )

    if subj_id is not None:
        clauses.append("t.subject_node_id = ?")
        params.append(subj_id)
    if pred_id is not None:
        clauses.append("t.predicate_id = ?")
        params.append(pred_id)
    if obj_id is not None:
        clauses.append("t.object_node_id = ?")
        params.append(obj_id)
    if provenance is not None:
        clauses.append("t.provenance = ?")
        params.append(provenance)
    if since is not None:
        clauses.append("t.observed_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("t.observed_at <= ?")
        params.append(until)
    if include_null_objects is True:
        clauses.append("t.object_node_id IS NULL")
    elif include_null_objects is False:
        clauses.append("t.object_node_id IS NOT NULL")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.extend([limit, offset])

    sql = (
        "SELECT t.id, t.subject_node_id, t.predicate_id, t.object_node_id,"
        "  t.observed_at, t.provenance, t.source_ref, t.created_at,"
        "  sn.label AS subject_label, sn.kind AS subject_kind,"
        "  p.name AS predicate_name,"
        "  on_.label AS object_label, on_.kind AS object_kind"
        " FROM triples t"
        "  JOIN nodes sn ON sn.id = t.subject_node_id"
        "  JOIN predicates p ON p.id = t.predicate_id"
        "  LEFT JOIN nodes on_ ON on_.id = t.object_node_id"
        f"{where}"
        " ORDER BY t.id DESC"
        " LIMIT ? OFFSET ?"
    )

    rows = conn.execute(sql, params).fetchall()
    return [_triple_row_to_dict(r) for r in rows]


def _query_with_inverse(
    conn: sqlite3.Connection,
    subj_id: int | None,
    pred_id: int,
    obj_id: int | None,
    provenance: str | None,
    since: str | None,
    until: str | None,
    include_null_objects: bool | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Query triples including the inverse predicate direction."""
    inv_row = conn.execute(
        "SELECT inverse_predicate_id, symmetric FROM predicates WHERE id = ?",
        (pred_id,),
    ).fetchone()

    forward = query_triples(
        conn,
        subject=subj_id,
        predicate=pred_id,
        object_=obj_id,
        provenance=provenance,
        since=since,
        until=until,
        include_null_objects=include_null_objects,
        include_inverse=False,
        limit=limit,
        offset=offset,
    )

    if not inv_row or not inv_row[0] or inv_row[1]:
        return forward

    inv_pred_id = inv_row[0]
    inverse = query_triples(
        conn,
        subject=obj_id,
        predicate=inv_pred_id,
        object_=subj_id,
        provenance=provenance,
        since=since,
        until=until,
        include_null_objects=include_null_objects,
        include_inverse=False,
        limit=limit,
        offset=offset,
    )

    combined = forward + inverse
    combined.sort(key=lambda t: t["triple_id"], reverse=True)
    return combined[:limit]


def _resolve_filter_id(
    conn: sqlite3.Connection,
    value: str | int | None,
    kind: str,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if kind == "predicate":
        pred = get_predicate(conn, value)
        return pred["id"] if pred else None
    row = conn.execute(
        "SELECT id FROM nodes WHERE normalized_label = ?",
        (_normalize(value),),
    ).fetchone()
    return row[0] if row else None


def _triple_row_to_dict(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "triple_id": row[0],
        "subject_node_id": row[1],
        "predicate_id": row[2],
        "object_node_id": row[3],
        "observed_at": row[4],
        "provenance": row[5],
        "source_ref": row[6],
        "created_at": row[7],
        "subject_label": row[8],
        "subject_kind": row[9],
        "predicate_name": row[10],
        "object_label": row[11],
        "object_kind": row[12],
    }


# === Qualifiers query ===


def get_qualifiers(
    conn: sqlite3.Connection,
    triple_id: int,
) -> list[dict[str, Any]]:
    """Return all qualifiers for a triple."""
    rows = conn.execute(
        "SELECT id, key, value FROM qualifiers WHERE triple_id = ?",
        (triple_id,),
    ).fetchall()
    return [{"id": r[0], "key": r[1], "value": r[2]} for r in rows]


# === Neighborhood / graph traversal ===


def node_neighborhood(
    conn: sqlite3.Connection,
    node: str | int,
    *,
    depth: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """Return the 1-hop (or multi-hop) neighborhood of a node.

    Returns ``{"node": {...}, "outgoing": [...], "incoming": [...]}``.
    """
    node_id = _resolve_filter_id(conn, node, "node")
    if node_id is None:
        return {"node": None, "outgoing": [], "incoming": []}

    node_row = conn.execute(
        "SELECT id, label, normalized_label, kind, vault_path,"
        " source_table, source_id"
        " FROM nodes WHERE id = ?",
        (node_id,),
    ).fetchone()

    node_info = {
        "id": node_row[0],
        "label": node_row[1],
        "kind": node_row[3],
        "vault_path": node_row[4],
        "source_table": node_row[5],
        "source_id": node_row[6],
    } if node_row else None

    outgoing = query_triples(conn, subject=node_id, limit=limit)
    incoming = query_triples(conn, object_=node_id, limit=limit)

    return {
        "node": node_info,
        "outgoing": outgoing,
        "incoming": incoming,
    }


# === Write-time emission ===

_EMIT_MAP: dict[str, tuple[str, bool]] = {
    "up": ("childOf", True),
    "links": ("relatesTo", True),
    "keywords": ("mentions", True),
    "tags": ("taggedWith", False),
}


def emit_for_frontmatter(
    conn: sqlite3.Connection,
    rel_path: str,
    frontmatter: dict[str, Any],
    *,
    provenance: str = "ai-emitted",
) -> int:
    """Emit triples from a file's frontmatter dict.

    Reads ``up:``, ``links:``, ``keywords:``, ``tags:`` and creates
    corresponding triples. Idempotent via add_triple's INSERT OR IGNORE.

    Returns count of newly created triples.
    """
    from pathlib import Path as _P

    file_label = _P(rel_path).stem
    subj_id = resolve_node(conn, file_label, "file", vault_path=rel_path)
    assert subj_id is not None

    created = 0
    for fm_key, (pred_name, is_wikilink) in _EMIT_MAP.items():
        fm_value = frontmatter.get(fm_key)
        if fm_value is None:
            continue

        targets: list[tuple[str, str]] = []
        if is_wikilink:
            for t in _extract_wikilink_labels(fm_value):
                if t:
                    targets.append((t, "concept"))
        elif isinstance(fm_value, list):
            for v in fm_value:
                s = str(v).strip()
                if s:
                    targets.append((s, "concept"))
        elif isinstance(fm_value, str) and fm_value.strip():
            targets.append((fm_value.strip(), "concept"))

        for obj_label, obj_kind in targets:
            result = add_triple(
                conn, subj_id, pred_name, obj_label,
                provenance=provenance,
                source_ref=rel_path,
                object_kind=obj_kind,
            )
            if result["created"]:
                created += 1

    # Handle predicate: key (note-fills-missing-role convention)
    pred_value = frontmatter.get("predicate")
    if pred_value:
        if isinstance(pred_value, str):
            pred_value = [pred_value]
        if isinstance(pred_value, list):
            for entry in pred_value:
                if not isinstance(entry, str):
                    continue
                result = _parse_predicate_entry(
                    conn, entry, subj_id, file_label,
                    provenance=provenance, source_ref=rel_path,
                )
                if result and result["created"]:
                    created += 1

    return created


def _parse_predicate_entry(
    conn: sqlite3.Connection,
    entry: str,
    file_node_id: int,
    file_label: str,
    *,
    provenance: str,
    source_ref: str,
) -> dict[str, Any] | None:
    """Parse a note-fills-missing-role predicate: entry.

    Three forms:
      "outOf [[Milk]]"      → file is subject, outOf is predicate, Milk is object
      "[[Rob]] outOf"        → Rob is subject, outOf is predicate, file is object
      "[[Rob]] [[Milk]]"     → Rob is subject, file-as-predicate, Milk is object
    """
    wikilinks = _WIKILINK_RE.findall(entry)
    bare = _WIKILINK_RE.sub("", entry).strip()

    if len(wikilinks) == 1 and bare:
        # Form 1 or 2: one wikilink + one bare word (predicate name)
        wl = wikilinks[0].strip()
        pred_name = bare

        # Determine order by position
        wl_pos = _WIKILINK_RE.search(entry)
        if wl_pos is None:
            return None
        bare_start = entry.find(bare)
        if bare_start < wl_pos.start():
            # "outOf [[Milk]]" → file is subject
            obj_id = resolve_node(conn, wl, "concept")
            return add_triple(
                conn, file_node_id, pred_name, wl,
                provenance=provenance, source_ref=source_ref,
                object_kind="concept",
            )
        else:
            # "[[Rob]] outOf" → file is object
            subj_id = resolve_node(conn, wl, "concept")
            if subj_id is None:
                return None
            return add_triple(
                conn, subj_id, pred_name, file_label,
                provenance=provenance, source_ref=source_ref,
                object_kind="concept",
            )
    elif len(wikilinks) == 2 and not bare:
        # Form 3: "[[Rob]] [[Milk]]" → file is the predicate
        subj_label = wikilinks[0].strip()
        obj_label = wikilinks[1].strip()
        subj_id = resolve_node(conn, subj_label, "concept")
        if subj_id is None:
            return None
        return add_triple(
            conn, subj_id, file_label, obj_label,
            provenance=provenance, source_ref=source_ref,
            object_kind="concept",
        )

    return None


def _extract_wikilink_labels(value: Any) -> list[str]:
    """Extract wikilink stems from a frontmatter value."""
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    targets: list[str] = []
    for v in values:
        if not isinstance(v, str):
            continue
        for m in _WIKILINK_RE.finditer(v):
            targets.append(m.group(1).strip())
    return targets


# === Stats ===


def triple_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return summary statistics for the triple store."""
    try:
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        triple_count = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
        null_object_count = conn.execute(
            "SELECT COUNT(*) FROM triples WHERE object_node_id IS NULL"
        ).fetchone()[0]
        qualifier_count = conn.execute("SELECT COUNT(*) FROM qualifiers").fetchone()[0]

        pred_usage = conn.execute(
            "SELECT p.name, COUNT(t.id) AS cnt"
            " FROM predicates p"
            " LEFT JOIN triples t ON t.predicate_id = p.id"
            " GROUP BY p.id ORDER BY cnt DESC"
        ).fetchall()

        kind_counts = conn.execute(
            "SELECT kind, COUNT(*) FROM nodes GROUP BY kind ORDER BY COUNT(*) DESC"
        ).fetchall()

        return {
            "nodes": node_count,
            "triples": triple_count,
            "null_object_triples": null_object_count,
            "qualifiers": qualifier_count,
            "predicates_by_usage": [
                {"name": r[0], "count": r[1]} for r in pred_usage
            ],
            "nodes_by_kind": [
                {"kind": r[0], "count": r[1]} for r in kind_counts
            ],
        }
    except sqlite3.OperationalError:
        return {"error": "predicate tables not yet created (migration 0012)"}
