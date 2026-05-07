# Contributing

## Development setup

```bash
git clone <repo-url>
cd personal-history-db
uv venv
uv pip install -e ".[dev]"
```

## Running tests

```bash
pytest                    # all tests
pytest tests/test_db.py   # single module
pytest -v                 # verbose
```

## Linting

```bash
ruff check src/ tests/    # lint
ruff check --fix src/     # auto-fix
mypy src/                 # type check
```

## Project structure

```
src/phdb/
  cli.py              Click CLI entrypoint
  db.py               Connection factory (WAL, busy_timeout, pragmas)
  settings.py          Three-tier settings (defaults -> TOML -> env)
  log.py               PII-sanitized logging
  migrations/
    runner.py          Migration runner (project 0001-0999, instance 1000+)
    project/           SQL migration files
  adapters/
    base.py            Adapter ABC + AdapterRow dataclass
    loader.py          Dynamic adapter discovery
  atoms/
    registry.py        Schema.org @type registry
```

## Writing an adapter

1. Subclass `Adapter` from `phdb.adapters.base`
2. Set `name`, `source_kind`, `file_kind` class attributes
3. Implement `iter_rows()` to yield `AdapterRow` instances
4. Override `parse_date()`, `compute_raw_hash()`, `detect_bulk()` as needed
5. The `run()` method handles source registration, batching, dedup, and commit

## Conventions

- Python 3.11+
- ruff for linting, mypy for type checking
- All timestamps are ISO-8601 with millisecond precision
- Every row carries a Schema.org `@type` via `schema_type`
- Dedup is via `INSERT OR IGNORE` on `(source_file_id, raw_hash)`
