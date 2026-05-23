<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

# phdb — Gemini context

This repository is `personal-history-db` (phdb) — a Python framework for
ingesting, indexing, and querying personal digital history across many
source formats. Tests use `pytest`; runtime is Python 3.11+; package
managed by `uv`.

## Active plan

The current work item is **Phase 7 — Plugin Port** of the
[phdb Plugin Architecture plan](../../Obsidian/Outputs/Plans/phdb%20Plugin%20Architecture.md).
Each spec-kit feature brief in `specs/queue/` is one plugin port: take
a legacy `src/phdb/adapters/<name>.py` and rewrite it as
`src/phdb/plugins/<name>/` under the new contract.

## Pre-flight reading (any plugin-port brief assumes you've read these)

Before touching the assigned adapter, load these files into context:

- `docs/plugins.md` — the PhdbPlugin contract spec.
- `src/phdb/plugins/raindrop/` — Phase 5 pilot worked example.
  Mirror its shape: `plugin.toml`, `plugin.py`, `ingest.py`, `tests/`.
- `src/phdb/core/plugin/` — `PluginManifest` dataclass, ABCs (Q4),
  loader (Q1).
- `src/phdb/schemas/canonical.py` — the 33 typed schemas your
  manifest's `emits = [...]` must resolve against at load time.
- `src/phdb/formats/url.py` — the shared-primitives precedent;
  manifests declare `formats_used = [...]` to make dependencies
  explicit.
- `specs/queue/README.md` — brief shape template + dispatch ordering.

## Hard rules

1. **Q14 hard break, no shim.** When porting `<name>`, delete the
   legacy `src/phdb/adapters/<name>.py` in the same change. Update any
   other adapter or test that imports from it to point at the new
   `phdb.plugins.<name>` home. Do NOT leave the old import path
   functional.
2. **Schemas live in `phdb.schemas/`, not in the plugin.** Plugins
   declare emission (`emits = ["EmailMessage", ...]`); they do NOT
   redefine schemas.
3. **Tests must stay green.** After each port, run
   `uv run pytest -x -q` — must pass. The full existing
   `tests/test_<name>_adapter.py` is the byte-clean golden-diff bar;
   every assertion ports verbatim.
4. **Entity-FK pattern (when applicable).** Plugins that touch URL
   bookmarks / browse / search must call `upsert_<entity>()` for the
   entity reference BEFORE inserting the action row. Junk/excluded
   action rows still create entity rows. See `phdb.plugins.raindrop.ingest`.
5. **PII auditor must pass.** PII regression failures are NEVER
   "unrelated" — fix immediately. See
   `tests/test_pii_regression.py` if any plugin port touches sample
   data.

## Commit conventions

Project uses the `changelog` skill convention. Per-commit format:

```
<scope>: <imperative summary>

<body explaining the why + design notes>

Source: Gemini (spec-kit pipeline)
```

The `<scope>` for plugin ports is the adapter name (`raindrop:`,
`apple_dbs:`, etc.).

## Run tests

```powershell
uv run pytest tests/ -x -q
```

## Common pitfalls (from memory)

- `bytes` literals (`b"..."`) cannot contain non-ASCII. If a TOML/JSON
  fixture has em-dashes or accents, decode/encode properly or use
  regular strings.
- Migration files are append-only; never edit a numbered migration
  after it ships. New migrations get the next free number.
- `phdb.core.plugin` is the sub-package; `phdb.core.plugin_loader` is
  the Phase-1 backward-compat re-export — prefer the sub-package.
- The `BookmarkEvent` record type is the canonical input shape for
  bookmark upserts; reuse it across raindrop + apple_dbs.

When in doubt, mirror what `phdb.plugins.raindrop` does.
