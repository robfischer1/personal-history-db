"""Cross-process write lock for phdb operations.

Prevents concurrent ingest + embed (or ingest + ingest, embed + embed)
against the same database.  Uses an OS-level file lock on a sidecar file
adjacent to the database.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class WriteLockError(RuntimeError):
    """Raised when the write lock cannot be acquired."""


def _lock_path(db_path: Path) -> Path:
    return db_path.with_suffix(db_path.suffix + ".phdb.lock")


def _is_pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _read_lock_info(lock_file: Path) -> tuple[int, str]:
    """Read PID and timestamp from an existing lock file."""
    try:
        text = lock_file.read_text(encoding="utf-8").strip()
        lines = text.splitlines()
        pid = int(lines[0]) if lines else -1
        ts = lines[1] if len(lines) > 1 else "unknown"
        return pid, ts
    except (ValueError, OSError):
        return -1, "unknown"


@contextmanager
def write_lock(db_path: Path | str, *, force: bool = False) -> Iterator[None]:
    """Acquire an exclusive write lock for the DB directory.

    Args:
        db_path: Path to the SQLite database file.
        force: If True, remove a stale lockfile (dead PID) before attempting.

    Raises:
        WriteLockError: If the lock is already held by another live process.
    """
    db_path = Path(db_path)
    lock_file = _lock_path(db_path)

    if lock_file.exists():
        pid, ts = _read_lock_info(lock_file)
        if pid > 0 and _is_pid_alive(pid):
            raise WriteLockError(
                f"Write lock held by PID {pid} (acquired {ts}). "
                f"Another phdb write operation is in progress."
            )
        if force or (pid > 0 and not _is_pid_alive(pid)) or pid <= 0:
            lock_file.unlink(missing_ok=True)
        else:
            raise WriteLockError(
                f"Stale lock file exists (PID {pid}, acquired {ts}). "
                f"Use --force to break it."
            )

    fd = None
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(
            f"{os.getpid()}\n{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
            encoding="utf-8",
        )

        fd = open(lock_file, "r+b")  # noqa: SIM115
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            fd.close()
            fd = None
            raise WriteLockError(f"Could not acquire write lock: {e}") from e

        yield
    finally:
        if fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fd.close()
        lock_file.unlink(missing_ok=True)
