"""Produce a verified consistent snapshot of the live phdb SQLite DB.

The phdb DB runs in WAL mode and is too large (~8 GB) and too live to safely
file-copy. This script uses SQLite's online backup API to copy a transactionally
consistent snapshot to a target file, then verifies integrity and emits a sidecar
manifest with row counts. The output is the input to `restic backup` (see
BACKUP.md).

Usage:
    uv run python scripts/snapshot_db.py --out-dir personal-history-data/snapshots
    uv run python scripts/snapshot_db.py --out-dir <dir> --db /custom/path.db
    uv run python scripts/snapshot_db.py --out-dir <dir> --dry-run

Reads PHDB_DB_PATH for the source DB by default. Writes:
    <out-dir>/snap-YYYYMMDD-HHMMSS.db          the snapshot
    <out-dir>/snap-YYYYMMDD-HHMMSS.json        manifest (integrity, row counts, sizes)
    <out-dir>/latest.db                         hardlink/copy of newest snapshot

Exit codes:
    0 - snapshot ok
    2 - integrity_check returned non-ok on the snapshot
    3 - row-count delta against previous snapshot looks anomalous (>1% loss)
    1 - other error (source missing, IO failure, etc.)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _source_db_path(cli_arg: str | None) -> Path:
    if cli_arg:
        return Path(cli_arg).resolve()
    env = os.environ.get("PHDB_DB_PATH")
    if env:
        return Path(env).resolve()
    raise SystemExit("error: --db not given and PHDB_DB_PATH not set")


def _integrity_check(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "<no result>"
    finally:
        conn.close()


def _row_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        counts: dict[str, int] = {}
        for t in tables:
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                # vec0 virtual tables don't support COUNT(*); skip
                continue
        return counts
    finally:
        conn.close()


def _take_snapshot(source: Path, target: Path) -> None:
    """Use SQLite's online backup API to copy source -> target.

    Safe against concurrent writers — SQLite holds the right shared locks.
    """
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst, pages=0)  # pages=0 -> copy in one transaction
        finally:
            dst.close()
    finally:
        src.close()


def _previous_manifest(out_dir: Path) -> dict | None:
    """Most recent prior manifest, if any, for sanity comparison."""
    manifests = sorted(out_dir.glob("snap-*.json"))
    if not manifests:
        return None
    return json.loads(manifests[-1].read_text(encoding="utf-8"))


def _anomalous_loss(prev: dict, current: dict[str, int]) -> list[str]:
    """Return tables that lost >1% of rows vs prior snapshot."""
    warnings: list[str] = []
    prev_counts = prev.get("row_counts", {})
    for table, prev_count in prev_counts.items():
        if prev_count == 0:
            continue
        cur_count = current.get(table, 0)
        loss = (prev_count - cur_count) / prev_count
        if loss > 0.01:
            warnings.append(
                f"{table}: {prev_count:,} -> {cur_count:,} ({loss * 100:.2f}% loss)"
            )
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", help="Source DB path; defaults to $PHDB_DB_PATH")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory to write the snapshot + manifest into",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run integrity_check on source only; do not write snapshot",
    )
    parser.add_argument(
        "--no-latest",
        action="store_true",
        help="Skip writing/updating the latest.db convenience copy",
    )
    args = parser.parse_args()

    source = _source_db_path(args.db)
    if not source.exists():
        print(f"error: source DB not found: {source}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir).resolve()

    if args.dry_run:
        print(f"[dry-run] source: {source}")
        print(f"[dry-run] source integrity_check: {_integrity_check(source)}")
        counts = _row_counts(source)
        print(f"[dry-run] source tables: {len(counts)}")
        for t, n in sorted(counts.items()):
            print(f"  {t}: {n:,}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snapshot = out_dir / f"snap-{stamp}.db"
    manifest_path = out_dir / f"snap-{stamp}.json"

    print(f"source: {source} ({source.stat().st_size:,} bytes)")
    print(f"target: {snapshot}")
    _take_snapshot(source, snapshot)
    print(f"snapshot written ({snapshot.stat().st_size:,} bytes)")

    integrity = _integrity_check(snapshot)
    print(f"integrity_check: {integrity}")
    if integrity != "ok":
        print("FAIL: snapshot failed integrity check; not finalizing", file=sys.stderr)
        return 2

    counts = _row_counts(snapshot)
    prev = _previous_manifest(out_dir)
    warnings = _anomalous_loss(prev, counts) if prev else []

    manifest = {
        "snapshot_path": str(snapshot),
        "source_path": str(source),
        "source_size_bytes": source.stat().st_size,
        "snapshot_size_bytes": snapshot.stat().st_size,
        "timestamp_utc": stamp,
        "integrity": integrity,
        "row_counts": counts,
        "previous_snapshot": prev.get("snapshot_path") if prev else None,
        "anomalous_loss_warnings": warnings,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"manifest: {manifest_path}")

    if not args.no_latest:
        latest = out_dir / "latest.db"
        if latest.exists():
            latest.unlink()
        # Copy not hardlink — restic chases hardlinks on some platforms
        # and we want latest.db to remain pointing at *this* snapshot even
        # if the dated file is later moved/deleted.
        latest.write_bytes(snapshot.read_bytes())
        print(f"latest: {latest}")

    if warnings:
        print("WARN: row-count anomalies vs previous snapshot:", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
        return 3

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
