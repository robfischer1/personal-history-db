"""One-time frontmatter extraction bootstrap for the predicate table.

Walks all in-scope vault .md files, parses frontmatter via vault-mcp's
parser, and maps up:/links:/keywords:/tags: to typed triples via phdb's
triples.py service module.

All triples carry provenance='extraction' and source_ref=<relative path>.

Usage:
    uv run python scripts/extract_frontmatter_triples.py --vault PATH [--dry-run]
    uv run python scripts/extract_frontmatter_triples.py --vault PATH --apply

Resumable: progress checkpoints to a JSON file after each batch.
Idempotent: uses INSERT OR IGNORE via triples.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_WORKSPACE = Path(__file__).resolve().parents[1].parent
_VAULT_MCP_SRC = _WORKSPACE / "vault-mcp" / "src"
if str(_VAULT_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_VAULT_MCP_SRC))

from vault_mcp.parsers import (  # noqa: E402
    build_content_index,
    extract_wikilink_targets,
)

from phdb.db import connect  # noqa: E402
from phdb.triples import add_triple, resolve_node  # noqa: E402

FRONTMATTER_MAP: dict[str, tuple[str, bool]] = {
    "up": ("childOf", True),
    "links": ("relatesTo", True),
    "keywords": ("mentions", True),
    "tags": ("taggedWith", False),
}

CHECKPOINT_FILENAME = "predicate-extraction-progress.json"


def _default_checkpoint_path() -> Path:
    return _WORKSPACE / "personal-history-db" / "runs" / CHECKPOINT_FILENAME


def _load_checkpoint(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"processed_files": [], "stats": {}}


def _save_checkpoint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_objects(
    fm_value: str | list | None,
    is_wikilink: bool,
) -> list[tuple[str, str]]:
    """Extract (label, kind) pairs from a frontmatter value.

    For wikilink fields, uses extract_wikilink_targets to get stems.
    For tag fields, uses the raw string as a concept label.
    """
    if fm_value is None:
        return []

    if is_wikilink:
        targets = extract_wikilink_targets(fm_value)
        return [(t, "concept") for t in targets if t]

    if isinstance(fm_value, list):
        return [(str(v).strip(), "concept") for v in fm_value if str(v).strip()]
    if isinstance(fm_value, str) and fm_value.strip():
        return [(fm_value.strip(), "concept")]
    return []


def extract_file(
    conn,
    rel_path: str,
    fm: dict,
    *,
    apply: bool = False,
) -> dict:
    """Extract triples from one file's frontmatter.

    Returns stats: {predicate_name: count_created}.
    """
    file_label = Path(rel_path).stem
    file_stats: dict[str, int] = {}

    if apply:
        subj_id = resolve_node(
            conn, file_label, "file",
            vault_path=rel_path,
        )
        assert subj_id is not None

    for fm_key, (pred_name, is_wikilink) in FRONTMATTER_MAP.items():
        fm_value = fm.get(fm_key)
        if fm_value is None:
            continue

        objects = _extract_objects(fm_value, is_wikilink)
        created_count = 0

        for obj_label, obj_kind in objects:
            if apply:
                result = add_triple(
                    conn,
                    subj_id,  # type: ignore[possibly-undefined]
                    pred_name,
                    obj_label,
                    provenance="extraction",
                    source_ref=rel_path,
                    object_kind=obj_kind,
                )
                if result["created"]:
                    created_count += 1
            else:
                created_count += 1

        if created_count:
            file_stats[pred_name] = created_count

    return file_stats


def run(
    vault_path: Path,
    db_path: Path,
    *,
    apply: bool = False,
    checkpoint_path: Path | None = None,
    batch_size: int = 200,
) -> dict:
    """Run the extraction pass.

    Returns a summary dict.
    """
    cp_path = checkpoint_path or _default_checkpoint_path()
    checkpoint = _load_checkpoint(cp_path)
    processed_set = set(checkpoint["processed_files"])

    print(f"Walking vault at {vault_path} ...")
    content, _by_name, _mtime = build_content_index(vault_path)
    total_files = len(content)
    already_done = sum(1 for _, _, rel in content if rel in processed_set)
    remaining = total_files - already_done
    print(f"Found {total_files} files with frontmatter, {already_done} already processed, {remaining} remaining")

    if remaining == 0:
        print("Nothing to do.")
        return {"total_files": total_files, "processed": 0, "skipped": already_done}

    summary: dict[str, int] = {
        "files_processed": 0,
        "triples_created": 0,
        "nodes_created_approx": 0,
    }
    pred_totals: dict[str, int] = {}

    with connect(db_path) as conn:
        batch_count = 0

        for _path, fm, rel_path in content:
            if rel_path in processed_set:
                continue

            file_stats = extract_file(conn, rel_path, fm, apply=apply)

            for pred_name, count in file_stats.items():
                pred_totals[pred_name] = pred_totals.get(pred_name, 0) + count
                summary["triples_created"] += count

            summary["files_processed"] += 1
            batch_count += 1

            if apply:
                processed_set.add(rel_path)

            if apply and batch_count >= batch_size:
                checkpoint["processed_files"] = sorted(processed_set)
                _save_checkpoint(cp_path, checkpoint)
                batch_count = 0
                print(f"  ... {summary['files_processed']}/{remaining} files processed")

        if apply and batch_count > 0:
            checkpoint["processed_files"] = sorted(processed_set)
            checkpoint["stats"] = {
                "last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pred_totals": pred_totals,
                **summary,
            }
            _save_checkpoint(cp_path, checkpoint)

    if apply:
        with connect(db_path) as conn:
            from phdb.triples import triple_stats
            stats = triple_stats(conn)
            summary["final_node_count"] = stats["nodes"]
            summary["final_triple_count"] = stats["triples"]

    summary["pred_totals"] = pred_totals  # type: ignore[assignment]

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"Extraction {mode} complete")
    print(f"  Files processed:  {summary['files_processed']}")
    print(f"  Triples {'created' if apply else 'would create'}:  {summary['triples_created']}")
    print("  By predicate:")
    for pred, count in sorted(pred_totals.items()):
        print(f"    {pred:20s} {count:>6d}")
    if apply:
        print(f"  Final node count:   {summary.get('final_node_count', '?')}")
        print(f"  Final triple count: {summary.get('final_triple_count', '?')}")
    print(f"{'='*60}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract frontmatter triples from vault into phdb predicate table",
    )
    parser.add_argument(
        "--vault", type=Path, required=True,
        help="Path to the Obsidian vault root",
    )
    parser.add_argument(
        "--db", type=Path,
        default=_WORKSPACE / "personal-history-data" / "personal-history.db",
        help="Path to personal-history.db",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write triples (default is dry-run)",
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=None,
        help="Path to checkpoint JSON (default: runs/predicate-extraction-progress.json)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=200,
        help="Checkpoint save frequency (files per batch)",
    )
    args = parser.parse_args()

    if not args.vault.is_dir():
        print(f"Error: vault path does not exist: {args.vault}", file=sys.stderr)
        sys.exit(1)
    if not args.db.exists():
        print(f"Error: database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    run(
        vault_path=args.vault,
        db_path=args.db,
        apply=args.apply,
        checkpoint_path=args.checkpoint,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
