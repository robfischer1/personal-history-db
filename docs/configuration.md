# Configuration Reference

## Overview

personal-history-db uses a three-tier configuration system:

1. **Code defaults** — Pydantic model defaults in `src/phdb/settings.py`
2. **Instance TOML** — files in your instance directory (e.g., `personal-history-instance/`)
3. **Environment variables** — `PHDB_`-prefixed env vars override everything

Higher-numbered tiers override lower. Instance config is optional — the framework works with defaults alone for testing.

## Environment variables

| Variable | Purpose | Example |
|:---|:---|:---|
| `PHDB_DB_PATH` | Path to the SQLite database | `/data/personal-history.db` |
| `PHDB_INSTANCE_DIR` | Path to the instance config directory | `/config/personal-history-instance` |
| `PHDB_LOG_LEVEL` | Logging level | `DEBUG`, `INFO`, `WARNING` |
| `PHDB_EMBEDDING__MODEL` | Ollama model name | `nomic-embed-text` |
| `PHDB_EMBEDDING__DIM` | Embedding dimensions | `768` |
| `PHDB_EMBEDDING__ENDPOINT` | Ollama API endpoint | `http://localhost:11434` |
| `PERSONAL_HISTORY_DB` | Legacy DB path (still honored as fallback) | `/data/personal-history.db` |

Nested settings use double-underscore delimiters (e.g., `PHDB_EMBEDDING__MODEL`).

## Instance TOML files

All `.toml` files in the instance directory are merged alphabetically. You can organize settings into multiple files or keep them in one.

### identity.toml

Owner identity for direction inference. Every adapter uses this to classify messages as inbound, outbound, or self.

```toml
[identity]
owner_names = ["Your Name", "Nickname", "your-handle"]
owner_emails = ["rob@example.com", "rob.fischer@work.com"]
owner_phones = ["+15551234567"]

[identity.owner_handles]
discord = ["robf#1234"]
facebook = ["rob.fischer"]
```

### paths.toml

Database and adapter search paths.

```toml
db_path = "C:/data/personal-history.db"
adapter_paths = ["C:/config/custom-adapters/"]
```

### embedding.toml

Embedding model configuration. Ships with `OllamaEmbedProvider`; the `EmbedProvider` Protocol allows adding custom backends.

```toml
[embedding]
model = "nomic-embed-text"
dim = 768
endpoint = "http://localhost:11434"
```

Requires a running [Ollama](https://ollama.ai/) instance with the configured model pulled (`ollama pull nomic-embed-text`).

Custom providers can be added by implementing the `EmbedProvider` Protocol in `src/phdb/embed_provider.py`. Each chunk stores its embedding metadata for future multi-provider coexistence.

### atoms.toml

Custom Schema.org `@type` mappings for instance-specific atom types.

```toml
[atoms]
dec = { type = "ChooseAction", description = "Decision moment" }
bio = { type = "Observation", description = "Biological measurement" }
```

## MCP server setup

The MCP server is a separate package — [personal-history-db-mcp](https://github.com/robfischer1/personal-history-db-mcp). It wraps the phdb query layer for AI assistant integration.

### Claude Code

Add to your project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "personal-history-db": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/robfischer1/personal-history-db-mcp.git", "personal-history-db-mcp"],
      "env": {
        "PHDB_DB_PATH": "/path/to/personal-history-data/personal-history.db",
        "PHDB_INSTANCE_DIR": "/path/to/personal-history-instance"
      }
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "personal-history-db": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/robfischer1/personal-history-db-mcp.git", "personal-history-db-mcp"],
      "env": {
        "PHDB_DB_PATH": "/path/to/personal-history-data/personal-history.db",
        "PHDB_INSTANCE_DIR": "/path/to/personal-history-instance"
      }
    }
  }
}
```

### Local development setup

If you have both repos checked out locally:

```json
{
  "mcpServers": {
    "personal-history-db": {
      "command": "uv",
      "args": ["--directory", "/path/to/personal-history-db-mcp", "run", "personal-history-db-mcp"],
      "env": {
        "PHDB_DB_PATH": "/path/to/personal-history-data/personal-history.db",
        "PHDB_INSTANCE_DIR": "/path/to/personal-history-instance"
      }
    }
  }
}
```

### Config resolution

The server resolves its database connection in this order:

1. `PHDB_DB_PATH` environment variable
2. `PHDB_INSTANCE_DIR` → loads `Settings` from TOML, uses `settings.db_path`
3. `PERSONAL_HISTORY_DB` environment variable (legacy, still honored)
4. `./personal-history.db` fallback

## CLI flags

Every CLI command accepts `--db` and `--instance-dir` to override config:

```bash
phdb --db /path/to/db --instance-dir /path/to/instance stats
phdb --db /path/to/db ingest /path/to/file.mbox --adapter mbox --dry-run
```

These override env vars and TOML config for that invocation.

## Migration numbering

| Range | Owner | Location |
|:---|:---|:---|
| `0001`–`0999` | Project | `src/phdb/migrations/project/` |
| `1000`+ | Instance | Instance directory (loaded at runtime) |

Both ranges are tracked in the `schema_migrations` table and applied in numeric order by `MigrationRunner.apply_pending()`.
