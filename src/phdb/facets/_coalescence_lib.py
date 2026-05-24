"""Shared coalescence primitives — Phase 8A.

Place-agnostic primitives both ``phdb.facets.people`` and
``phdb.facets.places`` consume. People-specific predicates and
geo-radius math live in the facet-side subclasses; this module is the
table-shaped contract that both facets bind against.

Architecture (per Phase 0 Q31 + Phase 8 plan):

- ``CoalescenceRule`` — one predicate + its confidence + bookkeeping.
  Rules express *equivalence judgments*: when do two emissions point
  at the same underlying entity?
- ``MergeProposal`` — the structured output of a rule firing. Names
  the canonical node to merge into, the emissions that contributed,
  and which rule fired with what confidence.
- ``AuditEntry`` — mirrors a row of the ``facet_coalescence_log``
  audit table. Each merge appends one entry; ``unmerge`` reads from
  this table to reverse.
- ``load_rules_from_toml`` — parse a TOML rule pack into evaluator-
  ready ``CoalescenceRule`` instances. Supports five rule shapes:
  ``exact_field`` (single-field equality with optional normalize),
  ``two_field`` (compound equality), ``regex`` (regex match on a
  field), ``geo_radius_meters`` (placeholder predicate; the real
  geo math lives in ``phdb.facets.places.coalescence``), and
  ``named_location`` (string-match on a place name field).
- ``Coalescer`` — proposal generator. Given rules + emissions,
  returns ``MergeProposal``s. **Does not write to DB.** The caller
  (a facet plugin's ``coalesce()`` impl) decides accept/reject and
  calls ``apply_merge`` for accepted proposals.
- ``write_audit_entry`` / ``apply_merge`` / ``unmerge`` — DB-write
  half. Table-agnostic via the ``node_table`` + ``fk_columns``
  arguments — same code path serves ``persons``-as-anchor merges and
  ``places``-as-anchor merges.

What this module deliberately omits (per Phase 8 scope split):

- People-specific predicates (email canonicalization, phone E.164,
  Discord handle case-folding) — those live in
  ``phdb.facets.people.coalescence``.
- Places-specific predicates (haversine geo-radius, named-place
  resolution) — those land in Phase 8B in
  ``phdb.facets.places.coalescence``.
- The interactive review CLI — Phase 8C wires that against the
  ``pending_review`` proposals this module's ``Coalescer`` produces.

Per Phase 4 ``facets.base``: the ``facet_coalescence_log`` table is
formalized in migration ``0029_facet_coalescence_log.sql`` (Phase 8A).
``ensure_audit_log`` is preserved as a fallback for legacy DBs that
haven't run the migration yet.
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CoalescenceRule:
    """One coalescence judgment — predicate + confidence + bookkeeping.

    ``predicate`` is a callable that accepts two emissions (or one
    emission + one DB row dict — both are duck-typed to a mapping of
    payload fields) and returns True when they represent the same
    underlying entity.

    ``shape`` records which rule shape was used to construct the
    predicate (``exact_field``, ``two_field``, ``regex``,
    ``geo_radius_meters``, ``named_location``). The TOML loader
    populates this for downstream introspection (e.g., the Phase 8C
    review CLI surfaces "which kind of rule fired?" to the user).
    """

    name: str
    predicate: Callable[[Any, Any], bool]
    confidence: float
    notes: str = ""
    shape: str = ""
    require_manual_review: bool = False
    fields: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class MergeProposal:
    """A structured proposal to merge ``from_emissions`` into ``into_node_id``.

    ``into_node_id`` is the canonical-survivor node id; ``from_emissions``
    are the emissions that the rule says equate to that node.
    ``confidence`` is taken from the rule that fired; the coalescer
    surfaces this so the caller can split into auto-merge (high
    confidence) vs. pending-review (low confidence) buckets.

    ``payload`` carries the merged-down field map used to construct
    the proposal; ``apply_merge`` persists this into the audit log so
    ``unmerge`` can recreate the original rows.
    """

    into_node_id: int
    from_emissions: list[Any]
    rule: str
    confidence: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditEntry:
    """One row of ``facet_coalescence_log``.

    ``payload`` is a JSON-serializable dict; ``apply_merge`` stores
    enough information here to reverse the merge via ``unmerge``
    (specifically: the merged-away node ids + the row data needed to
    recreate them).
    """

    facet_type: str
    facet_node_id: int
    rule_name: str
    confidence: float
    source_table: str | None = None
    source_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


def _emission_field(emission: Any, name: str) -> Any:
    """Lookup ``name`` on an emission — supports FacetEmission, dict, or row."""
    payload = getattr(emission, "payload", None)
    if isinstance(payload, dict) and name in payload:
        return payload[name]
    if isinstance(emission, dict):
        return emission.get(name)
    # Last resort: getattr
    return getattr(emission, name, None)


def _normalize(value: Any, normalize: str | None) -> Any:
    """Apply a named normalizer to a value."""
    if value is None:
        return None
    if normalize is None or normalize == "none":
        return value
    if normalize == "lowercase":
        return str(value).lower().strip()
    if normalize == "e164":
        # Conservative E.164-ish normalization mirroring
        # phdb.formats.phone_sms_sqlite._normalize_phone.
        import re

        a = re.sub(r"[\s\-().]", "", str(value).strip())
        if not a:
            return None
        if a.startswith("+"):
            return a
        if re.fullmatch(r"\d{10}", a):
            return "+1" + a
        if re.fullmatch(r"1\d{10}", a):
            return "+" + a
        return a
    if normalize == "strip":
        return str(value).strip()
    # Unknown normalizer — return raw
    return value


def _exact_field_predicate(
    field_name: str, normalize: str | None
) -> Callable[[Any, Any], bool]:
    def pred(a: Any, b: Any) -> bool:
        va = _normalize(_emission_field(a, field_name), normalize)
        vb = _normalize(_emission_field(b, field_name), normalize)
        if va is None or vb is None:
            return False
        if isinstance(va, str) and not va:
            return False
        return bool(va == vb)

    return pred


def _two_field_predicate(
    fields: tuple[str, ...], normalize: str | None
) -> Callable[[Any, Any], bool]:
    def pred(a: Any, b: Any) -> bool:
        for fname in fields:
            va = _normalize(_emission_field(a, fname), normalize)
            vb = _normalize(_emission_field(b, fname), normalize)
            if va is None or vb is None:
                return False
            if isinstance(va, str) and not va:
                return False
            if va != vb:
                return False
        return True

    return pred


def _regex_predicate(
    field_name: str, pattern: str
) -> Callable[[Any, Any], bool]:
    import re

    rx = re.compile(pattern)

    def pred(a: Any, b: Any) -> bool:
        va = _emission_field(a, field_name)
        vb = _emission_field(b, field_name)
        if va is None or vb is None:
            return False
        ma = rx.search(str(va))
        mb = rx.search(str(vb))
        if not ma or not mb:
            return False
        # Compare the matched groups (or the full match if no groups)
        ka = ma.group(1) if ma.groups() else ma.group(0)
        kb = mb.group(1) if mb.groups() else mb.group(0)
        return ka == kb

    return pred


def _geo_radius_placeholder() -> Callable[[Any, Any], bool]:
    """Placeholder for geo_radius_meters — real impl lands in places facet.

    Returns False unconditionally; the places facet (Phase 8B)
    subclasses ``Coalescer`` and overrides predicate construction for
    its own rule shapes.
    """

    def pred(a: Any, b: Any) -> bool:
        return False

    return pred


def _named_location_predicate(
    field_name: str, normalize: str | None = "lowercase",
) -> Callable[[Any, Any], bool]:
    """Named-location match — same name (normalized) = same place."""
    return _exact_field_predicate(field_name, normalize)


_PREDICATE_BUILDERS: dict[str, Callable[..., Callable[[Any, Any], bool]]] = {
    "exact_field": lambda fields, normalize, **_: _exact_field_predicate(
        fields[0], normalize,
    ),
    "two_field": lambda fields, normalize, **_: _two_field_predicate(
        tuple(fields), normalize,
    ),
    "regex": lambda fields, pattern, **_: _regex_predicate(
        fields[0], pattern,
    ),
    "geo_radius_meters": lambda **_: _geo_radius_placeholder(),
    "named_location": lambda fields, normalize, **_: _named_location_predicate(
        fields[0], normalize,
    ),
}


def build_predicate(rule_dict: dict[str, Any]) -> Callable[[Any, Any], bool]:
    """Build a predicate callable from a rule TOML dict.

    Exposed for facet-side subclasses (e.g., the places facet wants to
    register its own ``geo_radius_meters`` builder).
    """
    shape = rule_dict.get("shape", "exact_field")
    fields: list[str] = list(rule_dict.get("fields") or [])
    if "field" in rule_dict and not fields:
        fields = [rule_dict["field"]]
    normalize = rule_dict.get("normalize")
    pattern = rule_dict.get("pattern", "")
    builder = _PREDICATE_BUILDERS.get(shape)
    if builder is None:
        raise ValueError(f"unknown rule shape: {shape!r}")
    return builder(
        fields=fields,
        normalize=normalize,
        pattern=pattern,
    )


def _rule_from_dict(rule_dict: dict[str, Any]) -> CoalescenceRule:
    fields: list[str] = list(rule_dict.get("fields") or [])
    if "field" in rule_dict and not fields:
        fields = [rule_dict["field"]]
    return CoalescenceRule(
        name=rule_dict["name"],
        predicate=build_predicate(rule_dict),
        confidence=float(rule_dict.get("confidence", 0.5)),
        notes=str(rule_dict.get("notes", "")),
        shape=str(rule_dict.get("shape", "exact_field")),
        require_manual_review=bool(rule_dict.get("require_manual_review", False)),
        fields=tuple(fields),
    )


def load_rules_from_toml(
    path: Path,
    *,
    facet: str = "people",
) -> list[CoalescenceRule]:
    """Parse a TOML rule pack into evaluator-ready ``CoalescenceRule``s.

    The TOML file structure is::

        [[rules.<facet>]]
        name = "exact_email"
        shape = "exact_field"
        field = "email"
        normalize = "lowercase"
        confidence = 0.95

    Multiple ``[[rules.<facet>]]`` blocks accumulate. If the file does
    not exist, returns an empty list (caller decides whether to fall
    back to bundled defaults).
    """
    if not path.exists():
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    rules_block = data.get("rules", {})
    facet_rules = rules_block.get(facet, [])
    out: list[CoalescenceRule] = []
    for rd in facet_rules:
        out.append(_rule_from_dict(rd))
    return out


def load_rules_from_dicts(
    rule_dicts: Iterable[dict[str, Any]],
) -> list[CoalescenceRule]:
    """Construct rules from in-memory dicts — useful for bundled defaults."""
    return [_rule_from_dict(rd) for rd in rule_dicts]


# ---------------------------------------------------------------------------
# Coalescer — proposal generation (no DB writes)
# ---------------------------------------------------------------------------


@dataclass
class Coalescer:
    """Generate ``MergeProposal``s from rules + emissions.

    The base class is fully place-agnostic; people + places facets
    subclass to add facet-specific helpers (e.g., the people facet
    overrides ``_emission_key`` to compose a canonical-id key, and
    the places facet adds geo-radius batching).

    DB writes are caller-side — this class only proposes.
    """

    rules: list[CoalescenceRule]
    threshold: float = 0.0  # min confidence to surface in proposals

    def evaluate_pair(
        self, a: Any, b: Any
    ) -> CoalescenceRule | None:
        """Return the highest-confidence rule that fires for (a, b), or None."""
        best: CoalescenceRule | None = None
        for rule in self.rules:
            try:
                if (
                    rule.predicate(a, b)
                    and rule.confidence >= self.threshold
                    and (best is None or rule.confidence > best.confidence)
                ):
                    best = rule
            except Exception:  # pragma: no cover - defensive
                continue
        return best

    def evaluate_emission(
        self,
        emission: Any,
        candidates: Iterable[Any],
    ) -> list[tuple[Any, CoalescenceRule]]:
        """Score one emission against every candidate; return matches above threshold.

        Returns a list of (candidate, rule) tuples — one per candidate
        that matched at least one rule. The caller chooses how to
        resolve multiple matches (typical: take the highest-confidence
        rule).
        """
        out: list[tuple[Any, CoalescenceRule]] = []
        for cand in candidates:
            rule = self.evaluate_pair(emission, cand)
            if rule is not None:
                out.append((cand, rule))
        return out

    def coalesce_batch(
        self,
        emissions: list[Any],
        *,
        existing_nodes: Iterable[Any] = (),
    ) -> list[MergeProposal]:
        """Process a buffer of emissions; return merge proposals.

        Strategy: union-find over emissions + existing nodes. Each
        equivalence class collapses to one MergeProposal; the
        proposal's ``into_node_id`` is the smallest-id existing node
        in the class, or — if no existing node — a sentinel ``-1``
        meaning "create new node when applying."
        """
        items: list[Any] = list(existing_nodes) + list(emissions)
        n = len(items)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)

        # Track which rule fired for each merge so we can attribute
        # the highest-confidence rule to the resulting proposal.
        merge_rules: dict[tuple[int, int], CoalescenceRule] = {}
        for i in range(n):
            for j in range(i + 1, n):
                rule = self.evaluate_pair(items[i], items[j])
                if rule is not None:
                    union(i, j)
                    merge_rules[(i, j)] = rule

        # Group items by their root.
        groups: dict[int, list[int]] = {}
        for i in range(n):
            r = find(i)
            groups.setdefault(r, []).append(i)

        n_existing = len(list(existing_nodes)) if not isinstance(existing_nodes, list) else len(existing_nodes)
        # Recompute existing_nodes list len above is brittle if iterable consumed;
        # rebuild from items split point.
        existing_list = list(existing_nodes)
        n_existing = len(existing_list)

        proposals: list[MergeProposal] = []
        for _root, member_indices in groups.items():
            if len(member_indices) < 2:
                continue  # singletons aren't merges
            existing_members = [
                items[idx] for idx in member_indices if idx < n_existing
            ]
            emission_members = [
                items[idx] for idx in member_indices if idx >= n_existing
            ]
            if not emission_members:
                continue  # No new emissions in this class — nothing to merge

            into_id: int = -1
            # Collect node ids from BOTH explicit existing nodes AND any
            # emissions that carry an `id` in their payload (source
            # plugins emit with the persons.id they already inserted).
            all_ids: list[int] = []
            for n_ in existing_members:
                nid = _node_id(n_)
                if nid is not None:
                    all_ids.append(int(nid))
            for n_ in emission_members:
                nid = _node_id(n_)
                if nid is not None:
                    all_ids.append(int(nid))
            if all_ids:
                into_id = min(all_ids)

            # Pick the highest-confidence rule among any that fired for this class.
            class_rules = [
                merge_rules[(i, j)]
                for (i, j) in merge_rules
                if find(i) == find(member_indices[0])
            ]
            if not class_rules:
                continue
            best_rule = max(class_rules, key=lambda r: r.confidence)

            proposals.append(
                MergeProposal(
                    into_node_id=into_id,
                    from_emissions=emission_members,
                    rule=best_rule.name,
                    confidence=best_rule.confidence,
                    payload={
                        "shape": best_rule.shape,
                        "existing_count": len(existing_members),
                        "emission_count": len(emission_members),
                    },
                )
            )
        return proposals


def _node_id(node: Any) -> int | None:
    """Extract a node id from an emission/row/dict — duck-typed.

    Lookup order:
      1. ``id`` attr (existing-row case — DB rows from cursor.fetchall).
      2. ``payload["id"]`` (emission case — source plugins carry the
         already-inserted persons.id in the emission payload).
      3. ``payload["node_id"]`` (alternative key — some plugins prefer this).
      4. dict ``id`` key (raw-dict case).
    """
    if isinstance(node, dict):
        node_id = node.get("id")
        return int(node_id) if node_id is not None else None
    # FacetEmission has no ``id`` attr by default — check payload first.
    payload = getattr(node, "payload", None)
    if isinstance(payload, dict):
        if "id" in payload:
            pid = payload["id"]
            return int(pid) if pid is not None else None
        if "node_id" in payload:
            nid = payload["node_id"]
            return int(nid) if nid is not None else None
    attr_id = getattr(node, "id", None)
    return int(attr_id) if attr_id is not None else None


# ---------------------------------------------------------------------------
# DB-write half — table-agnostic
# ---------------------------------------------------------------------------


def write_audit_entry(conn: sqlite3.Connection, entry: AuditEntry) -> int:
    """Append one row to ``facet_coalescence_log``; return the row id."""
    cur = conn.execute(
        """INSERT INTO facet_coalescence_log
           (facet_type, facet_node_id, rule_name, confidence,
            source_table, source_id, payload)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.facet_type,
            entry.facet_node_id,
            entry.rule_name,
            entry.confidence,
            entry.source_table,
            entry.source_id,
            json.dumps(entry.payload, default=str),
        ),
    )
    rid = cur.lastrowid
    assert rid is not None
    return rid


def apply_merge(
    conn: sqlite3.Connection,
    node_table: str,
    proposal: MergeProposal,
    *,
    facet_type: str,
    fk_columns: list[tuple[str, str]] | None = None,
) -> int:
    """Atomically merge proposal into a canonical node; return surviving node id.

    Steps:
    1. Pick canonical survivor id (proposal.into_node_id, or smallest
       existing in payload if not provided).
    2. Update FK references in dependent tables to point at the
       survivor. ``fk_columns`` is a list of ``(table_name, column_name)``
       tuples that hold node ids in ``node_table``.
    3. Capture the merged-away rows' data into the audit payload so
       ``unmerge`` can recreate them.
    4. Delete the merged-away rows from ``node_table``.
    5. Write the audit entry.

    Returns the surviving node id.
    """
    fk_columns = fk_columns or []

    # 1. Determine survivor + merged-away ids.
    emission_ids: list[int] = [
        nid for e in proposal.from_emissions
        if (nid := _node_id(e)) is not None
    ]
    candidate_ids = list(emission_ids)
    if proposal.into_node_id >= 0:
        candidate_ids.append(proposal.into_node_id)
    if not candidate_ids:
        raise ValueError(
            "apply_merge: proposal has no node ids — nothing to merge"
        )
    survivor = min(candidate_ids)
    merged_away = [i for i in candidate_ids if i != survivor]
    if not merged_away:
        # Nothing to do — only the survivor was named.
        return survivor

    # 2. Capture row data of merged-away nodes for unmerge.
    placeholders = ",".join("?" for _ in merged_away)
    cur = conn.execute(
        f"SELECT * FROM {node_table} WHERE id IN ({placeholders})",
        merged_away,
    )
    cols = [c[0] for c in cur.description]
    captured_rows: list[dict[str, Any]] = [
        dict(zip(cols, r, strict=False)) for r in cur.fetchall()
    ]

    # 3. Update FK columns in dependent tables.
    for table, column in fk_columns:
        conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {column} IN ({placeholders})",
            [survivor, *merged_away],
        )

    # 4. Delete merged-away rows.
    conn.execute(
        f"DELETE FROM {node_table} WHERE id IN ({placeholders})",
        merged_away,
    )

    # 5. Write audit entry.
    audit_payload = {
        **proposal.payload,
        "merged_away_ids": merged_away,
        "captured_rows": captured_rows,
        "fk_columns": fk_columns,
        "node_table": node_table,
    }
    entry = AuditEntry(
        facet_type=facet_type,
        facet_node_id=survivor,
        rule_name=proposal.rule,
        confidence=proposal.confidence,
        source_table=node_table,
        source_id=survivor,
        payload=audit_payload,
    )
    write_audit_entry(conn, entry)
    conn.commit()
    return survivor


def unmerge(
    conn: sqlite3.Connection,
    node_table: str,
    audit_id: int,
) -> dict[str, Any]:
    """Reverse a single audit entry; return summary dict.

    Steps:
    1. Read the audit row; pull merged_away_ids + captured_rows.
    2. Re-insert the merged-away rows into ``node_table`` with their
       original ids (forces sqlite to honor the explicit id).
    3. For each (table, column) in fk_columns: the original FK target
       isn't reconstructible from a single-survivor audit log — we
       can only restore the rows themselves, not redistribute the
       FK rewrites. Document this in the summary.
    4. Delete the audit row.

    Returns a summary with restored_count + an explicit note when
    FK rewrites cannot be unwound from a single audit entry.
    """
    cur = conn.execute(
        "SELECT facet_node_id, payload FROM facet_coalescence_log WHERE id = ?",
        (audit_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"no audit entry with id {audit_id}")
    facet_node_id, payload_json = row
    payload = json.loads(payload_json) if payload_json else {}

    captured = payload.get("captured_rows", [])
    merged_away_ids = payload.get("merged_away_ids", [])
    audit_node_table = payload.get("node_table", node_table)
    if audit_node_table != node_table:
        raise ValueError(
            f"audit entry {audit_id} is for table {audit_node_table!r}, "
            f"not {node_table!r}"
        )

    restored = 0
    for r in captured:
        cols = list(r.keys())
        vals = [r[c] for c in cols]
        placeholders = ",".join("?" for _ in cols)
        col_list = ",".join(cols)
        conn.execute(
            f"INSERT OR IGNORE INTO {node_table} ({col_list}) VALUES ({placeholders})",
            vals,
        )
        restored += 1

    conn.execute("DELETE FROM facet_coalescence_log WHERE id = ?", (audit_id,))
    conn.commit()

    return {
        "audit_id": audit_id,
        "survivor_id": facet_node_id,
        "restored_ids": merged_away_ids,
        "restored_count": restored,
        "node_table": node_table,
        "note": (
            "Row data restored. FK references that were rewritten to the "
            "survivor remain on the survivor — single-audit unmerge cannot "
            "reconstruct the original FK distribution. Re-run coalescence "
            "to repopulate FKs if needed."
        ),
    }


__all__ = [
    "AuditEntry",
    "Coalescer",
    "CoalescenceRule",
    "MergeProposal",
    "apply_merge",
    "build_predicate",
    "load_rules_from_dicts",
    "load_rules_from_toml",
    "unmerge",
    "write_audit_entry",
]
