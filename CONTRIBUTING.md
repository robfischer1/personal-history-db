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
uv run pytest                     # all 1150+ tests
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
├── core/         # source-agnostic infrastructure (DB, embed, scoring, graph, search, plugin loader)
├── schemas/      # canonical Schema.org-keyed typed-table dataclasses
├── facets/       # facet plugins (people, places, time, threads, topics)
├── plugins/      # source plugins (34 first-party adapters)
├── formats/      # shared format parsers + upsert helpers
├── migrations/   # SQL migrations + runner
├── tools/        # CLI-facing utilities (coverage maps, sparsity, schema docs)
├── cli.py        # Click CLI entrypoint
├── settings.py   # Three-tier settings: defaults → instance TOML → env vars
└── query.py      # Unified query layer (hybrid search, lookups, people)
```

## Writing a plugin

See [docs/plugins.md](docs/plugins.md) for the plugin authoring guide. The
`phdb plugin scaffold <name>` command generates a skeleton with the manifest,
plugin class, tests, and ingest helper wired up.

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
