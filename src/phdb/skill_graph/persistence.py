"""Read/write skill-graph state to the phdb predicate table.

Disciplines are stored as ``concept`` nodes. Readiness is carried as
qualifiers on a ``(discipline, hasReadiness, NULL)`` triple — one such
triple per discipline. Writes use **upsert** semantics: if the triple
exists, its qualifiers are replaced; if not, the triple is created.

Why manual upsert: ``phdb.triples.add_triple`` is INSERT OR IGNORE and only
attaches qualifiers on initial creation. To replace readiness values on
re-compute, we delete and reinsert qualifiers explicitly.
"""

from __future__ import annotations

import sqlite3

from phdb.triples import add_triple, get_predicate, query_triples, resolve_node

from .models import DisciplineNode
from .vocabulary import (
    DISCIPLINE_KIND,
    PRED_HAS_READINESS,
    PRED_PREREQUISITE_OF,
    Q_BASE_VALUE,
    Q_DELEGATION_RECENT,
    Q_LAST_VERIFIED,
    Q_TIER,
    Q_VALUE,
)


def ensure_skill_graph_predicates(conn: sqlite3.Connection) -> None:
    """Insert the skill-graph predicates if not already present.

    ``childOf`` is reused from the existing predicate table; ``prerequisiteOf``
    and ``hasReadiness`` are new and may need seeding on a fresh DB.
    """
    _ensure_predicate(
        conn,
        PRED_PREREQUISITE_OF,
        description="Subject discipline must precede object discipline (skill-graph skeleton).",
    )
    _ensure_predicate(
        conn,
        PRED_HAS_READINESS,
        description="Placeholder triple for discipline readiness — value carried in qualifiers.",
    )


def _ensure_predicate(
    conn: sqlite3.Connection,
    name: str,
    *,
    description: str | None = None,
) -> int:
    """Find or create a predicate by name. Returns the predicate ID."""
    existing = get_predicate(conn, name)
    if existing is not None:
        return int(existing["id"])

    cur = conn.execute(
        "INSERT INTO predicates (name, description) VALUES (?, ?)",
        (name, description),
    )
    conn.commit()
    new_id = cur.lastrowid
    assert new_id is not None
    return int(new_id)


def read_discipline(conn: sqlite3.Connection, label: str) -> DisciplineNode | None:
    """Read the current readiness state for a discipline.

    Returns ``None`` if the discipline node doesn't exist OR has no
    ``hasReadiness`` triple. A node with a ``hasReadiness`` triple but no
    ``value`` qualifier yields ``readiness=None`` (still "unaddressed" in
    frontier terms).
    """
    node_id = resolve_node(conn, label, kind=DISCIPLINE_KIND, create=False)
    if node_id is None:
        return None

    triples = query_triples(
        conn,
        subject=node_id,
        predicate=PRED_HAS_READINESS,
        include_null_objects=True,
        limit=1,
    )
    if not triples:
        return None

    triple_id = int(triples[0]["triple_id"])
    qualifiers = _qualifiers_as_dict(conn, triple_id)

    return DisciplineNode(
        label=label,
        readiness=_parse_float(qualifiers.get(Q_VALUE)),
        last_verified=qualifiers.get(Q_LAST_VERIFIED),
        delegation_recent=_parse_bool(qualifiers.get(Q_DELEGATION_RECENT)),
    )


def write_readiness(
    conn: sqlite3.Connection,
    label: str,
    *,
    value: float | None,
    last_verified: str,
    delegation_recent: bool = False,
    base_value: float | None = None,
    tier: str | None = None,
) -> int:
    """Upsert the readiness state for a discipline.

    Creates the ``concept`` node + the ``(node, hasReadiness, NULL)`` triple
    if missing. Always replaces all qualifiers on the triple with the new
    payload — last_verified, delegation_recent, and (when present) value /
    base_value / tier.

    Returns the triple ID.
    """
    ensure_skill_graph_predicates(conn)
    node_id = resolve_node(conn, label, kind=DISCIPLINE_KIND, create=True)
    assert node_id is not None

    # Create the triple if missing (don't auto-attach qualifiers — we handle
    # both create and update via _replace_qualifiers below).
    result = add_triple(
        conn,
        node_id,
        PRED_HAS_READINESS,
        None,
        qualifiers=None,
    )
    triple_id = result["triple_id"]
    assert triple_id is not None

    payload = _build_qualifier_payload(
        value=value,
        last_verified=last_verified,
        delegation_recent=delegation_recent,
        base_value=base_value,
        tier=tier,
    )
    _replace_qualifiers(conn, int(triple_id), payload)
    return int(triple_id)


def _replace_qualifiers(
    conn: sqlite3.Connection,
    triple_id: int,
    qualifiers: list[dict[str, str]],
) -> None:
    """Delete all qualifiers on a triple, then reinsert the new payload."""
    conn.execute("DELETE FROM qualifiers WHERE triple_id = ?", (triple_id,))
    if qualifiers:
        conn.executemany(
            "INSERT INTO qualifiers (triple_id, key, value) VALUES (?, ?, ?)",
            [(triple_id, q["key"], q.get("value")) for q in qualifiers],
        )
    conn.commit()


def _qualifiers_as_dict(conn: sqlite3.Connection, triple_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM qualifiers WHERE triple_id = ?",
        (triple_id,),
    ).fetchall()
    return {r[0]: r[1] for r in rows if r[1] is not None}


def _parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _parse_bool(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.lower() in ("true", "1", "yes")


def _build_qualifier_payload(
    *,
    value: float | None,
    last_verified: str,
    delegation_recent: bool,
    base_value: float | None,
    tier: str | None,
) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = [
        {"key": Q_LAST_VERIFIED, "value": last_verified},
        {"key": Q_DELEGATION_RECENT, "value": "true" if delegation_recent else "false"},
    ]
    if value is not None:
        payload.append({"key": Q_VALUE, "value": f"{value:.4f}"})
    if base_value is not None:
        payload.append({"key": Q_BASE_VALUE, "value": f"{base_value:.4f}"})
    if tier is not None:
        payload.append({"key": Q_TIER, "value": tier})
    return payload
