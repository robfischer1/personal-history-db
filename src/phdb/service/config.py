"""Service configuration — load and validate service.toml from instance dir."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WatchJob:
    """A filesystem-watching job — triggers command on file changes."""

    name: str
    path: Path
    patterns: list[str] = field(default_factory=lambda: ["*.md"])
    ignore_dirs: list[str] = field(default_factory=list)
    debounce: int = 5
    command: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class ScheduleJob:
    """A time-scheduled job — triggers command on interval."""

    name: str
    interval: str  # "15m", "1h", "24h", "weekly"
    command: str = ""
    at: str | None = None  # "02:00" — time of day for daily/weekly
    day: str | None = None  # "sunday" — for weekly
    enabled: bool = True

    def interval_seconds(self) -> int:
        """Parse interval string into seconds."""
        m = re.match(r"^(\d+)\s*(s|m|h|d)$", self.interval.strip().lower())
        if m:
            val, unit = int(m.group(1)), m.group(2)
            mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
            return val * mult[unit]
        if self.interval.lower() in ("daily", "24h"):
            return 86400
        if self.interval.lower() == "weekly":
            return 604800
        msg = f"Cannot parse interval: {self.interval!r}"
        raise ValueError(msg)


@dataclass
class ServiceConfig:
    """Top-level service configuration."""

    watch_jobs: list[WatchJob] = field(default_factory=list)
    schedule_jobs: list[ScheduleJob] = field(default_factory=list)
    log_file: str = "service.log"
    pid_file: str = "service.pid"
    log_max_bytes: int = 5 * 1024 * 1024  # 5 MB
    log_backup_count: int = 3

    @classmethod
    def load(cls, path: Path) -> ServiceConfig:
        """Load from a service.toml file."""
        with open(path, "rb") as f:
            raw = tomllib.load(f)

        svc = raw.get("service", {})
        config = cls(
            log_file=svc.get("log_file", "service.log"),
            pid_file=svc.get("pid_file", "service.pid"),
            log_max_bytes=svc.get("log_max_bytes", 5 * 1024 * 1024),
            log_backup_count=svc.get("log_backup_count", 3),
        )

        for name, entry in svc.get("watch", {}).items():
            job = WatchJob(
                name=name,
                path=Path(entry["path"]),
                patterns=entry.get("patterns", ["*.md"]),
                ignore_dirs=entry.get("ignore_dirs", []),
                debounce=entry.get("debounce", 5),
                command=entry.get("command", ""),
                enabled=entry.get("enabled", True),
            )
            config.watch_jobs.append(job)

        for name, entry in svc.get("schedule", {}).items():
            job = ScheduleJob(
                name=name,
                interval=entry["interval"],
                command=entry.get("command", ""),
                at=entry.get("at"),
                day=entry.get("day"),
                enabled=entry.get("enabled", True),
            )
            config.schedule_jobs.append(job)

        return config

    def validate(self) -> list[str]:
        """Return a list of validation warnings (empty = valid)."""
        warnings: list[str] = []
        for wj in self.watch_jobs:
            if not wj.path.is_dir():
                warnings.append(f"watch/{wj.name}: path does not exist: {wj.path}")
            if not wj.command:
                warnings.append(f"watch/{wj.name}: no command specified")
        for sj in self.schedule_jobs:
            if not sj.command:
                warnings.append(f"schedule/{sj.name}: no command specified")
            try:
                sj.interval_seconds()
            except ValueError as e:
                warnings.append(f"schedule/{sj.name}: {e}")
        return warnings
