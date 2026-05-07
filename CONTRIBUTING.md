# Contributing

## Development setup

```bash
git clone https://github.com/robfischer1/personal-history-db.git
cd personal-history-db
uv venv
uv pip install -e ".[dev]"
```

## Running checks

```bash
uv run pytest                     # all 563 tests
uv run pytest tests/test_db.py    # single module
uv run pytest -k "test_bulk"      # by name pattern
uv run pytest -xvs                # verbose, stop on first failure

uv run ruff check src/ tests/     # lint
uv run ruff check --fix src/      # auto-fix safe issues
uv run ruff format src/ tests/    # format

uv run mypy src/                  # type check (strict mode)
```

All three must pass before submitting a PR.

## Project structure

```text
src/phdb/
├── cli.py                Click CLI entrypoint
├── db.py                 SQLite connection factory (WAL, pragmas, sqlite-vec)
├── settings.py           Three-tier settings: defaults → TOML → env vars
├── query.py              Unified query layer (hybrid search, lookups, people)
├── embed_pipeline.py     Chunking + batched Ollama embedding
├── embed_service.py      Ollama HTTP client
├── writelock.py          Cross-process file lock for DB writes
├── log.py                PII-redacted structured logging
├── validation.py         Instance config validation
├── adapters/
│   ├── base.py           Adapter ABC + AdapterRow + DedupStrategy
│   ├── loader.py         Dynamic adapter discovery from configured paths
│   └── *.py              32 source-format adapters
├── atoms/
│   └── registry.py       Schema.org @type registry
└── migrations/
    ├── runner.py          Migration runner (project 0001–0999, instance 1000+)
    └── project/           SQL migration files
```

## Writing an adapter

See [docs/writing-an-adapter.md](docs/writing-an-adapter.md) for the full guide.

Quick summary:

1. Subclass `Adapter` from `phdb.adapters.base`
2. Set `name`, `source_kind`, `file_kind`, `schema_type`, `dedup_strategy`
3. Implement `iter_rows()` to yield `AdapterRow` instances
4. Override `parse_date()`, `compute_raw_hash()`, `detect_bulk()` as needed
5. The `run()` method handles source registration, batching, dedup, and commit

## Conventions

- **Python 3.11+** with strict mypy
- **ruff** for linting: `select = ["E", "F", "W", "I", "UP", "B", "SIM"]`, line length 99
- **All timestamps** are ISO-8601 with timezone
- **Every message** carries a Schema.org `@type` via `schema_type`
- **Dedup** is via `INSERT OR IGNORE` on `(source_file_id, raw_hash)` — adapters must declare their strategy
- **Dry-run by default** — `--apply` required for real DB writes
- **Test fixtures are synthetic** — never commit real user data to `tests/fixtures/`
- **No PII in project tier** — identity config lives in instance TOML only

## Testing

Tests use synthetic fixtures in `tests/fixtures/`. To test an adapter:

```python
def test_my_adapter(tmp_path):
    from phdb.db import connect
    from phdb.migrations.runner import MigrationRunner
    from phdb.settings import Settings

    db_path = tmp_path / "test.db"
    with connect(db_path, load_vec=True) as conn:
        MigrationRunner(conn).apply_pending()

    settings = Settings.load(db_path=db_path)
    adapter = MyAdapter()

    with connect(db_path) as conn:
        report = adapter.run(fixture_path, conn, settings)
    assert report.rows_inserted == expected_count
```

## Migrations

- Project migrations go in `src/phdb/migrations/project/` numbered `0001`–`0999`
- Instance migrations go in the instance directory numbered `1000`+
- Use `MigrationRunner(conn).apply_pending()` to apply all pending migrations
- Always test migrations against an empty database and against one with existing data
