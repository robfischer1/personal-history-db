"""FileRevisionsPlugin — git-log capture walker for the file_revisions table.

Reads ``git log --reverse --raw --diff-filter=AMDR -- '*.md'`` against
the vault repo, parses the raw-diff blocks, and emits one row per
markdown file changed per commit into ``file_revisions`` (migration 0039).

After each row insert, the walker materializes both old and new blobs,
extracts the frontmatter + wikilink graph, and writes the predicate
deltas to ``revision_triple_deltas``. Phase 5 of the Git for Ideas plan
is folded into Phase 2 per the locked design.

Authorship is derived via ``phdb.authorship.get_authorship`` —
``rob-authored`` → ``rob``; anything else → ``ai``.

Idempotent on ``(repo, commit_sha, file_path)`` via UNIQUE INDEX +
``INSERT OR IGNORE``. Re-runs against an already-populated commit are
a no-op (use ``phdb revision rerun <sha>`` to force re-derivation).

CLI surface: ``phdb plugin run file_revisions [--repo NAME] [--since SHA]
[--limit N]``. The standard ``phdb plugin ingest`` entry point also works
(``source`` argument is ignored — git is the source, not a file).
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.authorship import get_authorship
from phdb.core.graph import _WIKILINK_RE, get_predicate, resolve_node
from phdb.core.plugin import PhdbSourcePlugin
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.file_revisions")


# ---------------------------------------------------------------------------
# Defaults + constants
# ---------------------------------------------------------------------------

DEFAULT_REPO = "vault"
DEFAULT_REPO_PATH = Path.home() / "Forge" / "Obsidian"

# Capture-worker log file. Lives alongside the DB so each repo's data dir
# can carry its own. Path-derivation matches the writing_deltas precedent.
DEFAULT_LOG_PATH = Path.home() / "Forge" / "personal-history-data" / "personal-history.file-revisions.log"


@contextmanager
def _file_log_handler(log_path: Path) -> Iterator[logging.Handler | None]:
    """Attach a per-run rotating FileHandler to the plugin logger.

    Uses ``RotatingFileHandler`` so the log doesn't grow unbounded
    across many walker runs. Rotation: 5 backups @ 5 MB each, keeping
    roughly 25 MB of historical lines (≈ a year of typical activity
    on a vault-scale repo). Errors opening the file are swallowed with
    a warning — the file handler is a convenience, not load-bearing.
    """
    handler: logging.Handler | None = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s %(message)s")
        )
        log.addHandler(handler)
        yield handler
    except OSError as exc:  # noqa: BLE001
        log.warning("[file_revisions] Could not open log file %s: %s", log_path, exc)
        yield None
    finally:
        if handler is not None:
            log.removeHandler(handler)
            handler.close()

# git log path exclusions — keep markdown only and drop noise pillars.
_PATHSPEC = [
    "--",
    "*.md",
    ":(exclude).obsidian/*",
    ":(exclude)attachments/*",
    ":(exclude).claude/*",
]

# Schema.org frontmatter keys that emit triples (mirrors phdb.core.graph._EMIT_MAP).
_FRONTMATTER_PREDICATE_KEYS: dict[str, tuple[str, bool]] = {
    "up": ("childOf", True),
    "links": ("relatesTo", True),
    "keywords": ("mentions", True),
    "tags": ("taggedWith", False),
}

# Default authorship classifier — `rob-authored` maps to `rob`, else `ai`.
_AUTHORSHIP_MAP: dict[str, str] = {
    "rob-authored": "rob",
    "ai-coauthored": "ai",
    "external": "ai",
}


# ---------------------------------------------------------------------------
# Parsing primitives
# ---------------------------------------------------------------------------


_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class _RawChange:
    """One row of git log --raw output."""

    status: str           # 'A' | 'M' | 'D' | 'R'
    sha_old: str          # 40-hex or '0' * 40
    sha_new: str          # 40-hex or '0' * 40
    path: str             # vault-relative POSIX path
    prior_path: str | None = None  # populated for rename


@dataclass
class _CommitBlock:
    """All raw-change rows for one commit."""

    sha: str
    changes: list[_RawChange] = field(default_factory=list)


def _parse_raw_log(stdout: str) -> Iterator[_CommitBlock]:
    """Parse the stdout of ``git log --reverse --raw --pretty=format:%H``.

    The format groups changes under each commit; we identify commit
    boundaries by the 40-hex line and the no-leading-colon shape.
    """
    current: _CommitBlock | None = None
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith(":"):
            if current is None:
                continue
            change = _parse_change_line(line)
            if change is not None:
                current.changes.append(change)
            continue
        # Otherwise this is the commit-sha header line.
        if _HEX40.match(line):
            if current is not None:
                yield current
            current = _CommitBlock(sha=line)
            continue
        # Anything else (shouldn't happen) — skip.
    if current is not None:
        yield current


def _parse_change_line(line: str) -> _RawChange | None:
    """Parse one ``:mode_a mode_b sha_a sha_b status\\tpath[\\tnew_path]`` line."""
    body = line[1:]  # strip leading colon
    if "\t" not in body:
        return None
    header, paths = body.split("\t", 1)
    parts = header.split()
    if len(parts) < 5:
        return None
    sha_old = parts[2]
    sha_new = parts[3]
    raw_status = parts[4]
    status = raw_status[0].upper() if raw_status else ""

    if status not in {"A", "M", "D", "R"}:
        return None

    path_parts = paths.split("\t", 1)
    if status == "R":
        if len(path_parts) != 2:
            return None
        prior_path, path = path_parts
        return _RawChange(
            status="R", sha_old=sha_old, sha_new=sha_new,
            path=path, prior_path=prior_path,
        )
    return _RawChange(status=status, sha_old=sha_old, sha_new=sha_new, path=path_parts[0])


def _change_type_for_status(status: str) -> str:
    return {"A": "add", "M": "modify", "D": "delete", "R": "rename"}[status]


# ---------------------------------------------------------------------------
# Frontmatter + wikilink parsing — minimal, no external deps
# ---------------------------------------------------------------------------


def _extract_frontmatter(body: str) -> dict[str, Any]:
    """Extract YAML frontmatter as a dict; empty dict on miss.

    Avoids pulling PyYAML for this single use case — frontmatter parsing
    here only needs to surface the four keys in ``_FRONTMATTER_PREDICATE_KEYS``
    plus the ``predicate:`` field, and those are all strings or lists of
    short strings. A minimal hand-roll is sufficient.
    """
    m = _FRONTMATTER_RE.match(body)
    if m is None:
        return {}
    return _parse_minimal_yaml(m.group(1))


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML — top-level keys + bulleted lists. No nesting.

    Handles three shapes:
        key: value
        key:
          - item
          - item
        key: [a, b, c]
    """
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in (" ", "\t"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip().strip('"').strip("'")
        value = value.strip()
        if value == "" or value == "[]":
            # Possible list block on next lines.
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    j += 1
                    continue
                if not nxt[0:1].isspace():
                    break
                stripped = nxt.lstrip()
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip().strip('"').strip("'"))
                    j += 1
                    continue
                break
            if items or value == "[]":
                result[key] = items
            else:
                result[key] = None
            i = j
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            items = [
                s.strip().strip('"').strip("'")
                for s in inner.split(",") if s.strip()
            ]
            result[key] = items
            i += 1
            continue
        result[key] = _strip_yaml_scalar(value)
        i += 1
    return result


def _strip_yaml_scalar(value: str) -> str:
    v = value.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    return v


def _scan_wikilinks(body: str) -> set[str]:
    """Return the set of wikilink targets in body text (post-frontmatter)."""
    return {m.group(1).strip() for m in _WIKILINK_RE.finditer(body)}


# ---------------------------------------------------------------------------
# Triple-set extraction
# ---------------------------------------------------------------------------


def _triples_for_body(body: str) -> set[tuple[str, str, str]]:
    """Return the canonical triple set for a markdown body.

    Each triple is (subject_label, predicate_name, object_label) where
    the subject is implicit (the file itself — populated by caller).
    We return only (predicate, object) pairs here keyed by an empty
    subject; the caller substitutes the file label as subject.
    """
    fm = _extract_frontmatter(body)
    triples: set[tuple[str, str, str]] = set()

    for fm_key, (pred_name, is_wikilink) in _FRONTMATTER_PREDICATE_KEYS.items():
        val = fm.get(fm_key)
        if val is None:
            continue
        if is_wikilink:
            objs = _extract_wikilink_targets(val)
        elif isinstance(val, list):
            objs = [str(v) for v in val if str(v).strip()]
        elif isinstance(val, str) and val.strip():
            objs = [val.strip()]
        else:
            objs = []
        for obj in objs:
            triples.add(("__self__", pred_name, obj))

    # Body-level wikilinks → mentions
    for target in _scan_wikilinks(body):
        triples.add(("__self__", "mentions", target))

    return triples


def _extract_wikilink_targets(value: Any) -> list[str]:
    """Pull wikilink stems out of a frontmatter value (str or list)."""
    if isinstance(value, str):
        return [m.group(1).strip() for m in _WIKILINK_RE.finditer(value)]
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if isinstance(v, str):
                out.extend(m.group(1).strip() for m in _WIKILINK_RE.finditer(v))
        return out
    return []


# ---------------------------------------------------------------------------
# Walker run summary
# ---------------------------------------------------------------------------


@dataclass
class FileRevisionsSummary:
    """Result of one walker run."""

    source_path: str
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    commits_processed: int = 0
    commits_skipped_no_changes: int = 0
    deltas_added: int = 0
    deltas_removed: int = 0
    unknown_predicates: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


class _BatchedBlobReader:
    """Persistent ``git cat-file --batch`` process for fast blob reads.

    Spawning ``git cat-file -p`` for every blob is the dominant cost of
    the walker (30K+ invocations on the vault). ``--batch`` mode reads
    SHAs on stdin and writes ``<sha> blob <size>\\n<bytes>\\n`` blocks
    on stdout — one git process for the whole run.

    Caller responsibility:
      - Always close the reader (``__exit__`` or ``close``) so the
        subprocess pipe doesn't leak.
      - Pass valid 40-char hex SHAs; this class doesn't validate.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._proc = subprocess.Popen(
            ["git", "-C", str(repo_root), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def __enter__(self) -> _BatchedBlobReader:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def read(self, blob_sha: str) -> str:
        """Return the body of one blob as a decoded UTF-8 string."""
        if self._proc.stdin is None or self._proc.stdout is None:
            return ""
        try:
            self._proc.stdin.write(f"{blob_sha}\n".encode("ascii"))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return ""
        header = self._proc.stdout.readline()
        if not header:
            return ""
        header_str = header.decode("utf-8", errors="replace").strip()
        # Header format: "<sha> blob <size>" on hit, "<sha> missing" on miss.
        parts = header_str.split()
        if len(parts) < 3 or parts[1] != "blob":
            return ""
        try:
            size = int(parts[2])
        except ValueError:
            return ""
        # ``read(n)`` on a pipe returns *up to* n bytes; must loop until size satisfied.
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self._proc.stdout.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        # Trailing newline after blob bytes.
        self._proc.stdout.read(1)
        return b"".join(chunks).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------


class FileRevisionsPlugin(PhdbSourcePlugin):
    """Git-log capture walker — emits file_revisions + revision_triple_deltas."""

    name = "file_revisions"
    source_kind = "git-log"
    file_kind = "git-repo"
    BATCH_SIZE = 200

    def __init__(self, manifest: Any = None) -> None:
        super().__init__(manifest)

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """A git repo isn't a single file — treat the root as the discovery target."""
        if (root / ".git").exists():
            yield root, self.source_kind

    def parse(self, path: Path) -> Iterator[dict[str, Any]]:
        """Yield one dict per commit-block from ``git log --raw``."""
        cmd = [
            "git", "-C", str(path),
            "log", "--reverse", "--raw",
            "--pretty=format:%H",
            "--diff-filter=AMDR",
            *_PATHSPEC,
        ]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            log.error(
                "[%s] git log failed: %s",
                self.name, result.stderr.decode("utf-8", errors="replace"),
            )
            return
        text = result.stdout.decode("utf-8", errors="replace")
        for block in _parse_raw_log(text):
            yield {"sha": block.sha, "changes": [
                {
                    "status": c.status, "sha_old": c.sha_old, "sha_new": c.sha_new,
                    "path": c.path, "prior_path": c.prior_path,
                }
                for c in block.changes
            ]}

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: dict[str, Any],
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Per-commit ingest — unused for direct row pipelines; run() is the entry."""
        return None

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
        *,
        repo: str = DEFAULT_REPO,
        since: str | None = None,
        limit: int | None = None,
        log_path: Path | None = None,
    ) -> FileRevisionsSummary:
        """Walk a git repo and emit file_revisions + delta rows.

        ``source_path`` is the path to the git checkout. If a falsy/
        sentinel value is passed (e.g. through the generic ``phdb plugin
        ingest`` CLI which expects a file), we fall back to
        ``DEFAULT_REPO_PATH``.

        Per-run logs are appended to ``log_path`` (default
        ``DEFAULT_LOG_PATH``). Existing rotation is age-based — the
        Phase 7 polish layer rotates by keeping the last 30 days; on
        each run the handler is attached for the duration of the run
        only.
        """
        target_log_path = log_path or DEFAULT_LOG_PATH
        with _file_log_handler(target_log_path):
            return self._run_inner(source_path, conn, settings, repo=repo, since=since, limit=limit)

    def _run_inner(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
        *,
        repo: str = DEFAULT_REPO,
        since: str | None = None,
        limit: int | None = None,
    ) -> FileRevisionsSummary:
        """Core walker body — split out so the FileHandler stays scoped per run."""
        repo_root = self._resolve_repo_root(source_path)
        report = FileRevisionsSummary(source_path=str(repo_root))

        # Ensure the commit_authorship_repos row exists and the repo_path
        # column reflects the live checkout. This catches the stale path
        # the column carried before the Forge/ relocation.
        self._upsert_repo_row(conn, repo, repo_root)

        # Catch-up commit_authorship if the walker would otherwise produce
        # rows for commits without authorship classification.
        self._catch_up_authorship(conn, repo, repo_root, since=since)

        commit_iter = self._iter_commits(repo_root, since=since)
        if limit is not None:
            commit_iter = (cb for i, cb in enumerate(commit_iter) if i < limit)

        batch_count = 0
        with _BatchedBlobReader(repo_root) as blob_reader:
            for block in commit_iter:
                report.commits_processed += 1
                if not block.changes:
                    report.commits_skipped_no_changes += 1
                    continue

                authorship_class = get_authorship(conn, repo, block.sha)
                authorship = _AUTHORSHIP_MAP.get(authorship_class, "ai")

                for change in block.changes:
                    report.rows_yielded += 1
                    try:
                        inserted_pk = self._insert_revision(
                            conn, repo=repo, commit_sha=block.sha,
                            change=change, authorship=authorship,
                        )
                    except Exception as exc:  # noqa: BLE001 — defensive log + continue
                        log.warning(
                            "[%s] insert failed sha=%s path=%s: %s",
                            self.name, block.sha[:8], change.path, exc,
                        )
                        report.errors.append(f"{block.sha[:8]} {change.path}: {exc}")
                        continue

                    if inserted_pk is None:
                        report.rows_skipped += 1
                        continue
                    report.rows_inserted += 1

                    # Phase 5 — triple deltas. Best-effort; bad encoding /
                    # parser glitches must not fail the row.
                    try:
                        added, removed = self._emit_triple_deltas(
                            conn, blob_reader=blob_reader,
                            rev_pk=inserted_pk, change=change,
                            unknown_predicates=report.unknown_predicates,
                        )
                        report.deltas_added += added
                        report.deltas_removed += removed
                    except Exception as exc:  # noqa: BLE001 — defensive
                        log.warning(
                            "[%s] triple delta failed sha=%s path=%s: %s",
                            self.name, block.sha[:8], change.path, exc,
                        )
                        report.errors.append(
                            f"delta {block.sha[:8]} {change.path}: {exc}"
                        )

                    batch_count += 1
                    if batch_count >= self.BATCH_SIZE:
                        conn.commit()
                        batch_count = 0

        conn.commit()

        log.info(
            "[%s] Done: repo=%s commits=%d rows_yielded=%d inserted=%d "
            "skipped=%d deltas+%d -%d unknown_preds=%d errors=%d",
            self.name, repo,
            report.commits_processed, report.rows_yielded,
            report.rows_inserted, report.rows_skipped,
            report.deltas_added, report.deltas_removed,
            len(report.unknown_predicates), len(report.errors),
        )
        return report

    # ----------------------------- Internals -------------------------------

    def _resolve_repo_root(self, source_path: Path) -> Path:
        """Map the CLI-supplied ``source_path`` to a real git repo root.

        The generic ``phdb plugin ingest <name> <path>`` CLI requires a
        path that exists; users will pass any directory. If the given
        path has no ``.git/``, fall back to ``DEFAULT_REPO_PATH``.
        """
        if source_path and (source_path / ".git").exists():
            return source_path.resolve()
        return DEFAULT_REPO_PATH.resolve()

    def _upsert_repo_row(
        self,
        conn: sqlite3.Connection,
        repo: str,
        repo_root: Path,
    ) -> None:
        """Insert or update the commit_authorship_repos row to match disk."""
        first_date_result = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--reverse",
             "--format=%aI", "--max-count=1"],
            capture_output=True, check=False,
        )
        first_date: str | None = None
        if first_date_result.returncode == 0:
            first_date = first_date_result.stdout.decode("utf-8", errors="replace").strip() or None

        conn.execute(
            """INSERT INTO commit_authorship_repos
                   (repo, repo_path, default_class, first_commit_date)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(repo) DO UPDATE SET
                   repo_path = excluded.repo_path,
                   first_commit_date = COALESCE(commit_authorship_repos.first_commit_date,
                                                excluded.first_commit_date)
            """,
            (repo, str(repo_root), "ai-coauthored", first_date),
        )
        conn.commit()

    def _catch_up_authorship(
        self,
        conn: sqlite3.Connection,
        repo: str,
        repo_root: Path,
        *,
        since: str | None,
    ) -> None:
        """Backfill commit_authorship for commits the trailer parser missed.

        The walker only needs ``rob-authored`` flagged explicitly when
        present — un-classified commits fall through to the repo
        default (``ai-coauthored``). This catch-up runs the trailer-
        based classifier for each commit not already in the table and
        inserts the explicit row, so the walker's per-row authorship
        lookup is correct without needing the heavier
        ``scripts/backfill_commit_authorship.py``.
        """
        # Find commits NOT in commit_authorship yet.
        rev_range = f"{since}..HEAD" if since else "HEAD"
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log",
             rev_range,
             "--format=%H%x00%aI%x00%s%x00%b%x00---END---"],
            capture_output=True, check=False,
        )
        if result.returncode != 0:
            log.warning(
                "[%s] commit_authorship catch-up — git log failed: %s",
                self.name,
                result.stderr.decode("utf-8", errors="replace").strip(),
            )
            return

        text = result.stdout.decode("utf-8", errors="replace")
        existing = {
            r[0] for r in conn.execute(
                "SELECT sha FROM commit_authorship WHERE repo = ?", (repo,)
            ).fetchall()
        }

        inserted = 0
        for block in text.split("---END---"):
            block = block.strip()
            if not block:
                continue
            parts = block.split("\x00", 3)
            if len(parts) < 4:
                continue
            sha, date, subject, body = parts
            sha = sha.strip()
            if sha in existing:
                continue

            has_co_authored = ("Co-Authored-By:" in body) or ("Co-authored-by:" in body)
            source_trailer: str | None = None
            for line in body.splitlines():
                if line.startswith("Source:"):
                    source_trailer = line.split(":", 1)[1].strip()
                    break

            if has_co_authored:
                cls, source = "ai-coauthored", "trailer"
            elif source_trailer == "Manual":
                cls, source = "rob-authored", "trailer"
            elif source_trailer:
                cls, source = "ai-coauthored", "trailer"
            else:
                # No trailer signal — let the repo-default rule handle it.
                continue

            conn.execute(
                """INSERT INTO commit_authorship
                       (repo, sha, authorship_class, source, commit_date, subject)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(repo, sha) DO UPDATE SET
                       authorship_class = excluded.authorship_class,
                       source = excluded.source
                """,
                (repo, sha, cls, source, date.strip(), subject.strip()),
            )
            inserted += 1
        if inserted:
            conn.commit()
            log.info(
                "[%s] commit_authorship catch-up: inserted %d rows for repo=%s",
                self.name, inserted, repo,
            )

    def _iter_commits(
        self,
        repo_root: Path,
        *,
        since: str | None,
    ) -> Iterator[_CommitBlock]:
        """Run ``git log --raw`` and yield parsed commit blocks."""
        cmd = [
            "git", "-C", str(repo_root),
            "log", "--reverse", "--raw",
            "--pretty=format:%H",
            "--diff-filter=AMDR",
        ]
        if since:
            cmd.append(f"{since}..HEAD")
        cmd.extend(_PATHSPEC)

        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            log.error(
                "[%s] git log failed: %s",
                self.name,
                result.stderr.decode("utf-8", errors="replace"),
            )
            return
        text = result.stdout.decode("utf-8", errors="replace")
        yield from _parse_raw_log(text)

    def _insert_revision(
        self,
        conn: sqlite3.Connection,
        *,
        repo: str,
        commit_sha: str,
        change: _RawChange,
        authorship: str,
    ) -> int | None:
        """Insert one file_revisions row; return PK on insert, None on duplicate."""
        change_type = _change_type_for_status(change.status)
        # For delete, the meaningful blob is the parent; we store
        # '0' * 40 in git_blob_sha to satisfy NOT NULL.
        git_blob_sha = change.sha_new
        parent_blob_sha: str | None = change.sha_old
        if change_type == "add":
            parent_blob_sha = None
        if change_type == "delete":
            # sha_new is already zeros; keep the sentinel.
            parent_blob_sha = change.sha_old

        cur = conn.execute(
            """INSERT OR IGNORE INTO file_revisions
                   (repo, commit_sha, file_path, git_blob_sha, parent_blob_sha,
                    change_type, authorship, prior_file_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo, commit_sha, change.path,
                git_blob_sha, parent_blob_sha,
                change_type, authorship,
                change.prior_path,
            ),
        )
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid or 0)

    def _emit_triple_deltas(
        self,
        conn: sqlite3.Connection,
        *,
        blob_reader: _BatchedBlobReader,
        rev_pk: int,
        change: _RawChange,
        unknown_predicates: set[str],
    ) -> tuple[int, int]:
        """Compute (added, removed) triple sets for a revision and persist deltas."""
        change_type = _change_type_for_status(change.status)
        file_stem = Path(change.path).stem
        # Subject node for triples is the file itself — resolve eagerly
        # so we can write subject_node_pk into the delta row.
        subject_node_pk = resolve_node(
            conn, file_stem, kind="file", vault_path=change.path,
        )

        new_body = ""
        old_body = ""
        if change_type in {"add", "modify", "rename"} and change.sha_new and set(change.sha_new) != {"0"}:
            new_body = blob_reader.read(change.sha_new)
        if change_type in {"modify", "delete", "rename"} and change.sha_old and set(change.sha_old) != {"0"}:
            old_body = blob_reader.read(change.sha_old)

        # For the prior file in a rename, the subject was the old stem.
        # Triples derived from old_body should be keyed under that
        # subject so the diff is correct.
        old_subject_stem = file_stem
        if change_type == "rename" and change.prior_path:
            old_subject_stem = Path(change.prior_path).stem

        old_set = _triples_for_body(old_body)
        new_set = _triples_for_body(new_body)

        # Rewrite the `__self__` placeholder to the real subject stem.
        old_set = {(old_subject_stem, p, o) for (_, p, o) in old_set}
        new_set = {(file_stem, p, o) for (_, p, o) in new_set}

        if change_type == "rename" and old_subject_stem != file_stem:
            # A rename's "same" triples count as remove(old)+add(new).
            added_set = new_set
            removed_set = old_set
        else:
            added_set = new_set - old_set
            removed_set = old_set - new_set

        added_count = self._write_deltas(
            conn, rev_pk=rev_pk, op="add", triples=added_set,
            subject_kind="file", default_subject_node_pk=subject_node_pk,
            unknown_predicates=unknown_predicates,
        )
        removed_count = self._write_deltas(
            conn, rev_pk=rev_pk, op="remove", triples=removed_set,
            subject_kind="file", default_subject_node_pk=subject_node_pk,
            unknown_predicates=unknown_predicates,
        )
        return added_count, removed_count

    def _write_deltas(
        self,
        conn: sqlite3.Connection,
        *,
        rev_pk: int,
        op: str,
        triples: set[tuple[str, str, str]],
        subject_kind: str,
        default_subject_node_pk: int | None,
        unknown_predicates: set[str],
    ) -> int:
        """Resolve subject/predicate/object IDs and write delta rows.

        Unknown predicates are recorded in ``unknown_predicates`` and
        the row is still inserted with ``predicate_pk = NULL`` so the
        delta is auditable even when the predicate vocabulary is
        incomplete.
        """
        written = 0
        for subj_label, pred_name, obj_label in triples:
            # Subject — resolve only if it differs from default (rename case).
            if subj_label == "__self__" or default_subject_node_pk is None:
                subj_node_pk = default_subject_node_pk
            elif default_subject_node_pk is not None:
                # Re-resolve when the subject label changed (rename).
                subj_node_pk = resolve_node(
                    conn, subj_label, kind=subject_kind,
                )
            else:
                subj_node_pk = None

            pred = get_predicate(conn, pred_name)
            if pred is None:
                unknown_predicates.add(pred_name)
                predicate_pk: int | None = None
            else:
                predicate_pk = pred["id"]

            obj_node_pk = resolve_node(conn, obj_label, kind="concept")

            conn.execute(
                """INSERT INTO revision_triple_deltas
                       (revision_pk, op, subject_node_pk, predicate_pk, object_node_pk)
                   VALUES (?, ?, ?, ?, ?)
                """,
                (rev_pk, op, subj_node_pk, predicate_pk, obj_node_pk),
            )
            written += 1
        return written
