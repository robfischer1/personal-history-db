# Fresh Start Guide

A complete walkthrough from zero to querying your personal history. This guide uses synthetic data so you can verify the setup works before pointing it at real exports.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Ollama](https://ollama.ai/) (for embeddings — optional for basic ingest, required for semantic search)

## 1. Install the project

```bash
git clone https://github.com/robfischer1/personal-history-db.git
cd personal-history-db
uv venv
uv pip install -e ".[dev]"
```

Verify the install:

```bash
phdb --version
phdb --help
```

## 2. Create your instance directory

The instance directory holds your personal config — identity, paths, embedding settings. It stays separate from the project so the project repo never contains PII.

```bash
mkdir -p ~/personal-history-instance
```

### identity.toml

This tells the framework which addresses are *yours*, enabling automatic direction inference (inbound vs. outbound vs. self).

```toml
# ~/personal-history-instance/identity.toml

[identity]
owner_names = ["Your Name"]
owner_emails = ["you@example.com"]
owner_phones = ["+15551234567"]

# Platform handles (optional)
[identity.owner_handles]
discord = ["yourname#1234"]
```

### paths.toml

```toml
# ~/personal-history-instance/paths.toml

db_path = "/home/you/personal-history-data/personal-history.db"
```

### embedding.toml

```toml
# ~/personal-history-instance/embedding.toml

[embedding]
model = "nomic-embed-text"
dim = 768
endpoint = "http://localhost:11434"
```

## 3. Create the database

```bash
mkdir -p ~/personal-history-data

phdb --instance-dir ~/personal-history-instance migrate
```

This creates the SQLite database at the path specified in `paths.toml` and applies all schema migrations.

## 4. Ingest some data

### Dry-run first

Every ingest command defaults to dry-run mode. This parses the source file and reports what *would* be inserted without touching the database.

```bash
phdb --instance-dir ~/personal-history-instance \
    ingest ~/takeout/All\ mail.mbox \
    --adapter mbox \
    --dry-run
```

The output shows row counts, date ranges, and any parse warnings.

### Apply for real

```bash
phdb --instance-dir ~/personal-history-instance \
    ingest ~/takeout/All\ mail.mbox \
    --adapter mbox \
    --apply
```

### Check the result

```bash
phdb --instance-dir ~/personal-history-instance stats
```

This shows total messages, source files, date coverage, and embedding status.

## 5. Embed for semantic search

Embedding requires a running Ollama instance with the configured model:

```bash
# Install and start Ollama (if not already running)
ollama pull nomic-embed-text
ollama serve

# Run the embed pipeline
phdb --instance-dir ~/personal-history-instance embed --apply
```

The embed pipeline chunks message bodies, sends them to Ollama in batches, and stores the vectors in the `doc_vectors` table.

## 6. Query

### CLI search

```bash
phdb --instance-dir ~/personal-history-instance \
    query "that conversation about moving to a new apartment"
```

Add date filters to narrow results:

```bash
phdb --instance-dir ~/personal-history-instance \
    query "job interview" --since 2023 --until 2024 --k 5
```

### MCP server

For AI assistant integration, start the MCP server:

```bash
PHDB_DB_PATH=~/personal-history-data/personal-history.db \
PHDB_INSTANCE_DIR=~/personal-history-instance \
    uv run python server.py
```

See [configuration.md](configuration.md) for Claude Code and Claude Desktop setup.

## Available adapters

Run `phdb ingest --help` to see adapter names. Some commonly used ones:

| Adapter | Source format | Notes |
|:---|:---|:---|
| `mbox` | Gmail/email `.mbox` export | Streaming parser, resume support |
| `imessage` | macOS `chat.db` | Apple Messages SQLite database |
| `discord` | Discord data package JSON | Channels, DMs, group chats |
| `facebook` | Facebook data download HTML | Messages from takeout |
| `facebook_posts` | Facebook posts HTML | Wall posts, status updates |
| `sms_xml` | SMS Backup & Restore XML | Android SMS/MMS export |
| `phone_sms` | Android `mmssms.db` | Raw telephony provider SQLite |
| `google_voice` | Google Voice takeout HTML | Calls, texts, voicemail |
| `spotify` | Spotify extended history JSON | Listening history |
| `strong` | Strong app SQLite export | Workout/exercise data |
| `apple_health` | Apple Health `export.xml` | Health & fitness records |
| `calendar` | Google Calendar takeout | Events from `.ics` |
| `goodreads` | Goodreads CSV export | Reading history |
| `raindrop` | Raindrop.io CSV export | Bookmarks |
| `staged_md` | Markdown files with frontmatter | Generic catch-all adapter |

## Troubleshooting

### "No such table: doc_vectors"

The `doc_vectors` table requires the sqlite-vec extension. Make sure you're using a connection with `load_vec=True`, or run `phdb migrate` which handles this automatically.

### "Write lock held by PID ..."

Another phdb process is writing to the database. Only one writer is allowed at a time. If the PID is stale (process no longer running), use `--force` to break the lock.

### Ollama connection refused

Make sure Ollama is running (`ollama serve`) and the endpoint in your `embedding.toml` matches. The default is `http://localhost:11434`.

## Next steps

- Ingest more sources — see the adapter list above
- Set up the MCP server for AI assistant access — see [configuration.md](configuration.md)
- Write a custom adapter for a source not yet supported — see [writing-an-adapter.md](writing-an-adapter.md)
