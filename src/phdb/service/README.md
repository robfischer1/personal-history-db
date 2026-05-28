# phdb service

Config-driven directory watchers and scheduled tasks for personal-history-db.
Replaces scattered Task Scheduler entries with a single process and one config file.

## Quick start

```
phdb service status          # show config + running state
phdb service start           # run foreground (Ctrl+C to stop)
phdb service run embed       # trigger one schedule job manually
```

## Configuration

The service reads `service.toml` from your instance directory
(`personal-history-instance/`). A template ships with `phdb init`.

Two job types:

### Watch jobs

Filesystem watchers using [watchdog](https://github.com/gorakhargosh/watchdog).
Triggers a command when matching files change, with configurable debounce.

```toml
[service.watch.vault-notes]
path = '/path/to/vault'
patterns = ["*.md"]
ignore_dirs = [".obsidian", ".git", "attachments"]
debounce = 10          # seconds — batch rapid changes
command = "phdb revision capture"
```

The command can include `{path}` (first changed file) or `{paths}` (all changed
files, space-separated and quoted).

### Schedule jobs

Interval-based tasks. Supported intervals: `30s`, `15m`, `1h`, `24h`, `daily`,
`weekly`.

```toml
[service.schedule.embed]
interval = "24h"
at = "02:00"           # informational — the service runs on interval from start
command = "phdb embed --limit 500"
enabled = true         # set false to skip without removing
```

### Global settings

```toml
[service]
log_file = "service.log"      # relative to data_dir
pid_file = "service.pid"      # relative to data_dir
log_max_bytes = 5242880       # 5 MB before rotation
log_backup_count = 3
```

## CLI reference

| Command | Description |
|:---|:---|
| `phdb service start` | Run service in foreground |
| `phdb service stop` | Send shutdown signal to a running instance (via PID file) |
| `phdb service status` | Show all watchers, schedules, and running state |
| `phdb service run <name>` | Execute a single schedule job immediately |
| `phdb service install` | Register as an NSSM Windows service |
| `phdb service uninstall` | Remove the NSSM Windows service |

Options on most commands:

- `--config <path>` — override config file (default: `instance_dir/service.toml`)

## Running as a Windows service

Requires [NSSM](https://nssm.cc/):

```powershell
# Install (admin shell)
$nssm = "path\to\nssm.exe"
& $nssm install phdb-service "path\to\.venv\Scripts\python.exe" "-m phdb service start"
& $nssm set phdb-service AppDirectory "path\to\workspace"
& $nssm set phdb-service DisplayName "phdb Service"
& $nssm start phdb-service
```

Set `AppDirectory` to the parent of `personal-history-instance/` so settings
discovery works.

## Dependencies

Directory watchers require `watchdog`:

```
pip install personal-history-db[service]
```

Schedule jobs use stdlib `threading.Timer` — no extra dependencies.

## Architecture

```
src/phdb/service/
├── __init__.py      # package
├── config.py        # ServiceConfig — TOML loader + validation
├── scheduler.py     # Scheduler — interval-based job runner (threading.Timer)
├── watcher.py       # WatcherManager — watchdog event handler with debounce
└── runner.py        # ServiceRunner — PID, signals, log rotation, orchestration
```

The runner starts all watchers and schedules, writes a PID file, and blocks
until SIGINT/SIGTERM/SIGBREAK. Graceful shutdown stops observers, cancels
timers, and removes the PID file.
