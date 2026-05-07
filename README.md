# personal-history-db

A framework for ingesting, deduplicating, and querying personal communication archives in SQLite.

## Architecture

Three-tier separation:

- **Project** (`src/phdb/`) — publishable framework code, adapters, migrations 0001-0999
- **Instance** (external) — PII-bearing config (identity, API keys), migrations 1000+
- **Data** (external) — SQLite database, embeddings, source files

## Quickstart

```bash
uv venv
uv pip install -e ".[dev]"

# Apply migrations to a new database
phdb migrate --db path/to/history.db

# Run an adapter
phdb ingest path/to/export.mbox --adapter mbox --db path/to/history.db

# Check database stats
phdb stats --db path/to/history.db
```

## Writing an adapter

```python
from pathlib import Path
from collections.abc import Iterator
from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy

class MyAdapter(Adapter):
    name = "my-source"
    source_kind = "chat"
    file_kind = "json"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs) -> Iterator[AdapterRow]:
        for record in load_my_data(source_path):
            yield AdapterRow(
                body_text=record["text"],
                sender_address=record["from"],
                date_sent=record["timestamp"],
            )
```

The `run()` method handles source registration, batching, dedup, direction inference, and commit.

## Development

```bash
pytest                    # run tests
ruff check src/ tests/    # lint
mypy src/                 # type check
```

## License

MIT
