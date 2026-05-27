"""Backfill file_revisions summaries via Qwen on llm01.

Hits llama-server's OpenAI-compatible API at llm01:8080 directly — no
Claude Code subagent overhead, no governance context tax. Runs parallel
requests up to llama-server's --parallel slot count (currently 2).

Usage:
    python scripts/qwen_summarize_backfill.py --dry-run     # preview queue
    python scripts/qwen_summarize_backfill.py --apply        # run backfill
    python scripts/qwen_summarize_backfill.py --apply --limit 100  # cap batch
    python scripts/qwen_summarize_backfill.py --apply --workers 2   # concurrency

Resumes automatically — rows with summary IS NULL are the queue.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PHDB_DB = Path(r"C:\Users\robfi\Forge\personal-history-data\personal-history.db")
LLAMA_URL = "http://100.88.96.20:8080/v1/chat/completions"
MODEL_TAG = "qwen-llm01"
REQUEST_TIMEOUT = 120

SYSTEM_PROMPT = (
    "You write 2-4 sentence change summaries for a markdown vault's git "
    "history. Each call shows you the prior body and the current body of "
    "one note. Describe what changed and why — architectural intent, "
    "semantic shift, structural reshape, governance move. Focus on "
    "producer intent. Do not narrate the diff line by line. Do not "
    "include preamble; start the summary directly. Output ONLY the "
    "summary text — no headers, no quotes, no explanation."
)

MAX_BODY_BYTES = 32 * 1024


@dataclass
class Row:
    rev_id: int
    file_path: str
    change_type: str
    git_blob_sha: str
    parent_blob_sha: str | None


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(PHDB_DB), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_repo_root(conn: sqlite3.Connection) -> Path:
    row = conn.execute(
        "SELECT repo_path FROM commit_authorship_repos WHERE repo = 'vault'"
    ).fetchone()
    if not row:
        sys.exit("No vault repo registered in commit_authorship_repos")
    return Path(row[0])


def git_cat_file(repo_root: Path, sha: str) -> str:
    import subprocess
    if not sha or set(sha) == {"0"}:
        return ""
    result = subprocess.run(
        ["git", "cat-file", "-p", sha],
        cwd=repo_root,
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def truncate(body: str) -> tuple[str, bool]:
    encoded = body.encode("utf-8")
    if len(encoded) <= MAX_BODY_BYTES:
        return body, False
    return encoded[:MAX_BODY_BYTES].decode("utf-8", errors="ignore"), True


def build_prompt(row: Row, old_body: str, new_body: str) -> str:
    old_trim, old_trunc = truncate(old_body)
    new_trim, new_trunc = truncate(new_body)

    def fence(label: str, body: str, trunc: bool) -> str:
        if not body:
            return f"### {label}\n(empty)"
        note = f" (truncated)" if trunc else ""
        return f"### {label}{note}\n```markdown\n{body}\n```"

    if row.change_type == "add":
        prior = "### Prior body\n(no prior — first revision)"
        current = fence("Current body", new_trim, new_trunc)
    elif row.change_type == "delete":
        prior = fence("Prior body", old_trim, old_trunc)
        current = "### Current body\n(deleted)"
    else:
        prior = fence("Prior body", old_trim, old_trunc)
        current = fence("Current body", new_trim, new_trunc)

    return (
        f"File: `{row.file_path}`\n"
        f"Change type: `{row.change_type}`\n\n"
        f"{prior}\n\n{current}\n\n"
        "Write the 2-4 sentence summary now. Output only the summary text."
    )


def call_qwen(prompt: str) -> str | None:
    payload = json.dumps({
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 512,
    }).encode("utf-8")

    req = Request(
        LLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        if text.startswith("<think>"):
            end = text.find("</think>")
            if end != -1:
                text = text[end + len("</think>"):].strip()
        return text if text else None
    except (HTTPError, URLError, KeyError, json.JSONDecodeError) as e:
        print(f"  [ERROR] Qwen call failed: {e}", file=sys.stderr)
        return None
    except TimeoutError:
        print("  [ERROR] Qwen call timed out", file=sys.stderr)
        return None


def fetch_queue(conn: sqlite3.Connection, limit: int) -> list[Row]:
    rows = conn.execute(
        "SELECT id, file_path, change_type, git_blob_sha, parent_blob_sha"
        " FROM file_revisions"
        " WHERE summary IS NULL"
        " ORDER BY captured_at ASC"
        " LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        Row(rev_id=r[0], file_path=r[1], change_type=r[2],
            git_blob_sha=r[3], parent_blob_sha=r[4])
        for r in rows
    ]


def process_one(row: Row, repo_root: Path) -> tuple[int, str | None]:
    if row.change_type in {"modify", "rename"}:
        old_body = git_cat_file(repo_root, row.parent_blob_sha or "")
        new_body = git_cat_file(repo_root, row.git_blob_sha)
    elif row.change_type == "add":
        old_body = ""
        new_body = git_cat_file(repo_root, row.git_blob_sha)
    elif row.change_type == "delete":
        old_body = git_cat_file(repo_root, row.parent_blob_sha or "")
        new_body = ""
    else:
        old_body = ""
        new_body = git_cat_file(repo_root, row.git_blob_sha)

    combined = len(old_body.encode("utf-8")) + len(new_body.encode("utf-8"))
    if combined == 0:
        return row.rev_id, None

    prompt = build_prompt(row, old_body, new_body)
    summary = call_qwen(prompt)
    return row.rev_id, summary


def record(conn: sqlite3.Connection, rev_id: int, summary: str) -> None:
    conn.execute(
        "UPDATE file_revisions"
        " SET summary = ?,"
        "     summary_model = ?,"
        "     summary_generated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        " WHERE id = ?",
        (summary, MODEL_TAG, rev_id),
    )
    conn.commit()


def record_skip(conn: sqlite3.Connection, rev_id: int) -> None:
    conn.execute(
        "UPDATE file_revisions"
        " SET summary = 'blob unreadable or empty',"
        "     summary_model = 'skip:blob-unreadable',"
        "     summary_generated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        " WHERE id = ?",
        (rev_id,),
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill file_revisions summaries via Qwen on llm01")
    parser.add_argument("--apply", action="store_true", help="Actually run (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Preview queue only")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to process (0 = all)")
    parser.add_argument("--workers", type=int, default=2, help="Concurrent requests (match llama-server --parallel)")
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        parser.print_help()
        sys.exit(1)

    conn = connect()
    total_remaining = conn.execute(
        "SELECT COUNT(*) FROM file_revisions WHERE summary IS NULL"
    ).fetchone()[0]

    print(f"Queue: {total_remaining} unsummarized rows")

    if args.dry_run:
        sample = fetch_queue(conn, min(10, total_remaining))
        for r in sample:
            print(f"  rev_id={r.rev_id}  {r.change_type:8s}  {r.file_path}")
        if total_remaining > 10:
            print(f"  ... and {total_remaining - 10} more")
        conn.close()
        return

    limit = args.limit if args.limit > 0 else total_remaining
    queue = fetch_queue(conn, limit)
    repo_root = get_repo_root(conn)

    print(f"Processing {len(queue)} rows with {args.workers} workers")
    print(f"Target: llama-server at {LLAMA_URL}")
    print()

    done = 0
    skipped = 0
    failed = 0
    t0 = time.monotonic()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_one, row, repo_root): row
            for row in queue
        }
        for future in as_completed(futures):
            row = futures[future]
            try:
                rev_id, summary = future.result()
            except Exception as e:
                print(f"  [FAIL] rev_id={row.rev_id} {row.file_path}: {e}", file=sys.stderr)
                failed += 1
                continue

            if summary is None:
                record_skip(conn, rev_id)
                skipped += 1
            else:
                record(conn, rev_id, summary)
                done += 1

            total = done + skipped + failed
            elapsed = time.monotonic() - t0
            rate = total / elapsed if elapsed > 0 else 0
            eta = (len(queue) - total) / rate if rate > 0 else 0

            if total % 10 == 0 or total == len(queue):
                print(
                    f"  [{total}/{len(queue)}] "
                    f"done={done} skip={skipped} fail={failed} "
                    f"rate={rate:.1f}/s "
                    f"ETA={eta/60:.0f}m"
                )

    elapsed = time.monotonic() - t0
    conn.close()

    print()
    print(f"Complete in {elapsed/60:.1f} minutes")
    print(f"  Summarized: {done}")
    print(f"  Skipped:    {skipped}")
    print(f"  Failed:     {failed}")


if __name__ == "__main__":
    main()
