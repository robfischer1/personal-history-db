# Instance Template

Copy this directory to create your personal-history-db instance configuration.

```bash
cp -r instance-template/ ~/personal-history-instance/
cd ~/personal-history-instance/
git init
```

Then edit each `.toml` file with your actual values:

- **identity.toml** — your names, email addresses, phone numbers, and platform handles. Used for direction inference (classifying messages as inbound/outbound/self).
- **paths.toml** — database path, adapter discovery paths, log level.
- **embedding.toml** — Ollama model, dimension, and endpoint for semantic search.
- **atoms.toml** — custom Schema.org @types that extend the project's canonical registry.
- **sources.toml** — registry of your data sources and their file paths.

Place instance-private adapters in `adapters/` and instance-specific SQL migrations (numbered 1000+) in `migrations/`.

Run with:

```bash
phdb --instance-dir ~/personal-history-instance/ migrate --db ~/personal-history-data/personal-history.db
phdb --instance-dir ~/personal-history-instance/ ingest --adapter mbox ~/data/All-Mail.mbox
```
