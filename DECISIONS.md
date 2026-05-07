---
created: 2026-05-06
updated: 2026-05-06
status: Phase 0 decisions resolved
type: project-decisions
related:
  - "[[REWRITE_PLAN]]"
---

# Personal-History-DB — Decisions Log

Phase 0 decisions and any subsequent in-flight changes. One entry per decision. Decisions are durable; if circumstances change, add a new entry rather than editing the old one.

## Decision template

```
### YYYY-MM-DD — <decision name>

**Status:** [pending | decided | superseded by <ref>]
**Decided by:** <Rob | Opus + Rob | etc.>

**Context:** <one paragraph>

**Options considered:**
- <option 1>: pros, cons
- <option 2>: pros, cons

**Decision:** <what was chosen>

**Rationale:** <why>

**Consequences:** <what this commits us to>
```

---

## Decisions made

### 2026-05-06 — Project name

**Status:** decided
**Decided by:** Opus + Rob

**Context:** New project repo will be public on GitHub. Needs a name distinct from Rob's instance, descriptive enough that adopters understand purpose.

**Decision:** `personal-history-db` for the project name. CLI command is `phdb` (short alias).

**Rationale:** Descriptive; already what governance docs reference; the rename cost is real and the current name is fine.

**Consequences:** PyPI package name `personal-history-db`; GitHub repo `personal-history-db`; binary entry point `phdb`.

### 2026-05-06 — Project / Instance / Data paths

**Status:** decided
**Decided by:** Opus + Rob (Q3 alternative chosen)

**Context:** Three-tier separation requires three on-disk locations. Should they be co-located or split across drives?

**Decision:**
- Project: `C:\Users\<owner>\Obsidian\personal-history-db\`
- Instance: `C:\Users\<owner>\Obsidian\personal-history-instance\`
- Data: `C:\Users\<owner>\Obsidian\personal-history-data\`

All three are siblings of the vault, sharing the `~\Obsidian\` parent.

**Rationale:** Single mental-model parent dir; one backup target; no cross-drive sync coordination. Data dir must be excluded from Obsidian Sync explicitly (does not auto-inherit vault sync).

**Consequences:**
- All three paths must be added to Obsidian Sync's excluded folders list
- Each gets its own `.gitignore` strategy: project (full git), instance (private git or local-only), data (no git)
- The current DB at vault root (`C:\Users\<owner>\Obsidian\Obsidian\personal-history.db`) moves to `C:\Users\<owner>\Obsidian\personal-history-data\personal-history.db` in Phase 7

### 2026-05-06 — Config format

**Status:** decided
**Decided by:** Opus + Rob

**Context:** Instance tier holds people/identity tables, source registration, atom @type declarations, paths, embedding config.

**Decision:** TOML for instance config files; Pydantic models for project-side schemas.

**Rationale:** Native Python 3.11+ TOML support; readable; Pydantic-settings handles the layered merge (defaults → package → instance) cleanly; schema validation at framework startup catches drift.

**Consequences:** Instance config dir contains `*.toml` files (one per concern: `paths.toml`, `identity.toml`, `sources.toml`, `atoms.toml`, etc.). Project ships Pydantic settings classes that load these.

### 2026-05-06 — Adapter discovery mechanism

**Status:** decided
**Decided by:** Opus + Rob

**Context:** The loader needs to find adapters in both project and instance dirs.

**Decision:** Configured Python path in instance config (`adapter_paths = [...]`). Loader scans listed paths for `Adapter` subclasses.

**Rationale:** Simpler than entry points; sufficient for a one-user system; explicit. Entry points can be added later if Rob ever wants to package adapters as installable plugins.

**Consequences:** Instance config has an `adapter_paths` list pointing at `instance/adapters/` (and any other dirs). Project's adapter loader walks each path looking for `Adapter` subclasses by introspection.

### 2026-05-06 — License

**Status:** decided
**Decided by:** Rob

**Context:** Project repo is public.

**Decision:** MIT.

**Rationale:** Most permissive, simplest, widely understood, lowest friction for any future adopter.

**Consequences:** `LICENSE` file with MIT text in project repo.

### 2026-05-06 — Embedding model

**Status:** decided
**Decided by:** Opus + Rob

**Context:** Currently `nomic-embed-text` (768-dim) via Ollama. Re-embedding 220K chunks costs time + compute; upgrading would require it.

**Decision:** Keep `nomic-embed-text` (768-dim) for now. Make the model pluggable via instance config.

**Rationale:** Re-embedding mid-rewrite adds risk without benefit. Preserving search behavior is valuable for behavior-preservation testing. Pluggability means a future swap is a config change, not a code change.

**Consequences:**
- Instance config: `embedding.model = "nomic-embed-text"`, `embedding.dim = 768`, `embedding.endpoint = "http://localhost:11434"`
- Project framework reads these and constructs the embedder at startup
- Phase 5 query module respects the pinned model when calling Ollama

### 2026-05-06 — Atom @types: canonical vs instance-specific

**Status:** decided
**Decided by:** Opus + Rob

**Context:** Some @types ship with the project; others are Rob-specific. Where does the line live?

**Decision:** Schema.org standard @types are project canonical. Rob-specific atom subtypes (custom slugs) are instance.

**Concretely:**
- Project: `Dataset`, `EmailMessage`, `Conversation`, `DigitalDocument`, `BookmarkAction`, `BefriendAction`, `Message`, `CreativeWork`, `ListenAction`, plus any other Schema.org @types in active use
- Instance: `dec` slug (ChooseAction subtype), and any other custom slugs Rob has invented

**Rationale:** Schema.org is the canonical vocabulary. Rob's invented slugs are personal modeling choices. Clean rule: *if it's in Schema.org, it's project; if it's your invention, it's instance.*

**Consequences:**
- Project ships a registry of canonical Schema.org @types with validation rules
- Instance can extend with custom @types declared in `atoms.toml`
- Phase 3 extracts existing custom slugs into instance config

### 2026-05-06 — Migration namespacing

**Status:** decided
**Decided by:** Opus + Rob

**Context:** Both project and instance can ship migrations. Naive numbered migrations collide.

**Decision:** Numbered ranges. Project owns `0001-0999`. Instance owns `1000+`.

**Rationale:** Simplest viable solution; sufficient for one instance; easy to upgrade to authored migrations later if multiple instances appear.

**Consequences:**
- Project migration files: `0001_init.sql`, `0002_*.sql`, ...
- Instance migration files (in instance dir): `1000_*.sql`, `1001_*.sql`, ...
- Migration runner reads both dirs, sorts by number, applies in order
- Existing migrations 001–005 renumber to 0001–0005 during Phase 1

### 2026-05-06 — Chunk format versioning

**Status:** decided
**Decided by:** Opus + Rob

**Context:** If chunking strategy changes, do existing embeddings need to be invalidated?

**Decision:** Defer. Preserve current chunking strategy verbatim through the rewrite.

**Rationale:** The `chunk_strategy` field on `documents` already supports versioning naturally (mixed strategies can coexist). No need to touch it during the rewrite. Behavior-preservation testing is easier when chunking is unchanged.

**Consequences:**
- Phase 4 ports must produce byte-identical chunks to legacy on identical inputs
- `chunk_strategy` values seen in the existing DB are preserved verbatim
- Future chunking strategy changes get new `chunk_strategy` values; old chunks coexist until re-chunked

---

## Open decisions

(None — Phase 0 questions all resolved 2026-05-06.)

## Superseded

(None yet.)
