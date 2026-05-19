"""Predicate-stub renderer — ephemeral hub-notes for Obsidian graph view.

For each predicate with triples, generates a hub-note at
Atlas/Predicates/<predicate-name>.md whose body radiates wikilinks to
every subject and object. Obsidian's native graph view then shows the
predicate as a hub node with subject/object pairs around it.

Stubs are ephemeral + regenerable — never durable vault content.

Usage:
    uv run python scripts/render_predicate_stubs.py --vault PATH --db PATH [--dry-run]
    uv run python scripts/render_predicate_stubs.py --vault PATH --db PATH --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_phdb_src = str(Path(__file__).resolve().parent.parent / "src")
if _phdb_src not in sys.path:
    sys.path.insert(0, _phdb_src)

from phdb.db import connect
from phdb.triples import list_predicates, query_triples


def render_stub(
    predicate_name: str,
    triples: list[dict],
    *,
    generated_at: str | None = None,
) -> str:
    """Render a predicate hub-note as a string."""
    if generated_at is None:
        generated_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    pairs: list[tuple[str, str | None]] = []
    for t in triples:
        subj = t["subject_label"]
        obj = t.get("object_label")
        pairs.append((subj, obj))

    lines = [
        "---",
        '"@context": "https://schema.org"',
        '"@type": "CollectionPage"',
        f'name: "Predicate: {predicate_name}"',
        f'identifier: "predicate-stub-{predicate_name}"',
        f'date: {datetime.now().strftime("%Y-%m-%d")}',
        'author_type: "ai-generated"',
        'execution_type: recomputed',
        f'generated_at: "{generated_at}"',
        'ephemeral: true',
        "status: Active",
        "tags: []",
        'up: "[[Atlas]]"',
        "---",
        "",
        f"## {predicate_name}",
        "",
        f"> [!INFO] Ephemeral hub-note",
        f"> Auto-generated from the predicate table. {len(pairs)} triples.",
        f"> Regenerate with `render_predicate_stubs.py`.",
        "",
    ]

    if pairs:
        lines.append("| Subject | Object |")
        lines.append("| :--- | :--- |")
        for subj, obj in pairs:
            subj_link = f"[[{subj}]]"
            obj_link = f"[[{obj}]]" if obj else "_(open)_"
            lines.append(f"| {subj_link} | {obj_link} |")
        lines.append("")

    return "\n".join(lines)


def run(
    vault_path: Path,
    db_path: Path,
    *,
    apply: bool = False,
    predicate_filter: str | None = None,
    min_triples: int = 1,
) -> dict:
    """Render predicate stubs.

    Returns summary dict.
    """
    stubs_dir = vault_path / "Atlas" / "Predicates"
    generated_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    with connect(db_path) as conn:
        preds = list_predicates(conn)
        rendered = 0
        skipped = 0

        for p in preds:
            name = p["name"]
            if predicate_filter and name != predicate_filter:
                continue

            triples = query_triples(conn, predicate=name, limit=10000)
            if len(triples) < min_triples:
                skipped += 1
                continue

            content = render_stub(name, triples, generated_at=generated_at)
            stub_path = stubs_dir / f"{name}.md"

            if apply:
                stubs_dir.mkdir(parents=True, exist_ok=True)
                stub_path.write_text(content, encoding="utf-8", newline="\n")
                print(f"  wrote {stub_path.relative_to(vault_path)} ({len(triples)} triples)")
            else:
                print(f"  [dry-run] would write {name}.md ({len(triples)} triples)")

            rendered += 1

    return {
        "rendered": rendered,
        "skipped": skipped,
        "apply": apply,
    }


def main():
    ap = argparse.ArgumentParser(description="Render predicate hub-notes.")
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--predicate", default=None, help="Render only this predicate.")
    ap.add_argument("--min-triples", type=int, default=1,
                    help="Skip predicates with fewer triples (default: 1).")
    args = ap.parse_args()

    print(f"Predicate stub renderer")
    print(f"  vault: {args.vault}")
    print(f"  db:    {args.db}")
    print(f"  mode:  {'APPLY' if args.apply else 'DRY-RUN'}")
    print()

    result = run(
        args.vault, args.db,
        apply=args.apply,
        predicate_filter=args.predicate,
        min_triples=args.min_triples,
    )

    print()
    print(f"Done: {result['rendered']} rendered, {result['skipped']} skipped.")


if __name__ == "__main__":
    main()
