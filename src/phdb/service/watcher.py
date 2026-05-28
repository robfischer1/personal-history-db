"""Directory watcher — debounced filesystem event handler using watchdog."""

from __future__ import annotations

import fnmatch
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phdb.service.config import WatchJob

log = logging.getLogger(__name__)

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    FileSystemEventHandler = object  # type: ignore[assignment,misc]


@dataclass
class WatcherState:
    """Tracks runtime state for a watch job."""

    job: WatchJob
    last_trigger: datetime | None = None
    last_result: int | None = None
    last_error: str | None = None
    trigger_count: int = 0
    pending_paths: list[str] = field(default_factory=list)


def _run_command(command: str, paths: list[str]) -> tuple[int, str]:
    """Execute a command, substituting {paths} and {path} placeholders."""
    if "{paths}" in command:
        expanded = command.replace("{paths}", " ".join(f'"{p}"' for p in paths))
    elif "{path}" in command:
        expanded = command.replace("{path}", paths[0] if paths else "")
    else:
        expanded = command

    try:
        result = subprocess.run(
            expanded,
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            stderr_tail = result.stderr[-500:] if result.stderr else ""
            return result.returncode, stderr_tail
        return 0, ""
    except subprocess.TimeoutExpired:
        return -1, "command timed out after 600s"
    except Exception as e:
        return -2, str(e)


class _DebouncedHandler(FileSystemEventHandler):
    """Collects filesystem events, fires command after debounce period."""

    def __init__(self, state: WatcherState, stop_event: threading.Event) -> None:
        super().__init__()
        self._state = state
        self._stop = stop_event
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def _matches(self, path: str) -> bool:
        p = Path(path)
        for ignore in self._state.job.ignore_dirs:
            if ignore in p.parts:
                return False
        return any(fnmatch.fnmatch(p.name, pattern) for pattern in self._state.job.patterns)

    def _enqueue(self, path: str) -> None:
        if not self._matches(path):
            return
        with self._lock:
            if path not in self._state.pending_paths:
                self._state.pending_paths.append(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(
                self._state.job.debounce,
                self._flush,
            )
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            paths = list(self._state.pending_paths)
            self._state.pending_paths.clear()
            self._timer = None

        if not paths:
            return

        log.info("watch/%s: %d file(s) changed, running command", self._state.job.name, len(paths))
        code, err = _run_command(self._state.job.command, paths)

        self._state.last_trigger = datetime.now(UTC)
        self._state.last_result = code
        self._state.last_error = err if code != 0 else None
        self._state.trigger_count += 1

        if code == 0:
            log.info("watch/%s: command succeeded (%d files)", self._state.job.name, len(paths))
        else:
            log.error("watch/%s: command failed (exit %d): %s", self._state.job.name, code, err)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.src_path)
            if hasattr(event, "dest_path"):
                self._enqueue(event.dest_path)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class WatcherManager:
    """Manages multiple directory watchers."""

    def __init__(self) -> None:
        self._states: dict[str, WatcherState] = {}
        self._observers: list[Any] = []
        self._handlers: list[_DebouncedHandler] = []
        self._stop_event = threading.Event()

    def add(self, job: WatchJob) -> None:
        self._states[job.name] = WatcherState(job=job)

    def start(self) -> None:
        if not HAS_WATCHDOG:
            log.warning("watchdog not installed — directory watchers disabled. pip install watchdog")
            return

        self._stop_event.clear()
        for name, state in self._states.items():
            if not state.job.enabled:
                log.info("watch/%s: disabled, skipping", name)
                continue
            if not state.job.path.is_dir():
                log.warning("watch/%s: path does not exist: %s", name, state.job.path)
                continue

            handler = _DebouncedHandler(state, self._stop_event)
            observer = Observer()
            observer.schedule(handler, str(state.job.path), recursive=True)
            observer.daemon = True
            observer.start()

            self._observers.append(observer)
            self._handlers.append(handler)
            log.info("watch/%s: watching %s", name, state.job.path)

    def stop(self) -> None:
        self._stop_event.set()
        for handler in self._handlers:
            handler.cancel()
        for observer in self._observers:
            observer.stop()
        for observer in self._observers:
            observer.join(timeout=5)
        self._observers.clear()
        self._handlers.clear()

    def status(self) -> list[WatcherState]:
        return list(self._states.values())
