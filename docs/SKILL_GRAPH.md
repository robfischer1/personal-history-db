# Skill Graph schema

> Phase 1 schema for the Skill Graph plan (vault-side: `Outputs/Plans/Skill Graph.md`). The skill graph is a layered application over phdb's predicate table (migration 0012). No new tables, no new migration — vocabulary + qualifier conventions only. `add_triple` already supports the `qualifiers` parameter; no extension needed.

## Conceptual model

A **discipline** is a node. Disciplines may be **composite** (with children via `childOf`) or **leaf**. They have **readiness** — a leaky-integrator value in `[0.0, 1.0]` carried as qualifiers on a `hasReadiness` triple. The structural skeleton is built from `prerequisiteOf` edges (subject must precede object).

The skill graph layer does **not** introduce new tables — it uses existing predicate-table primitives:

- `nodes` — disciplines are `kind="concept"` nodes.
- `predicates` — three predicates: `prerequisiteOf` (skeleton, new), `childOf` (composition; existing), `hasReadiness` (readiness carrier, new).
- `triples` — encode structural relations and the readiness placeholder.
- `qualifiers` — carry per-triple readiness values, timestamps, and flags.

## Vocabulary

### Node kinds

| Kind | Use |
| :--- | :--- |
| `concept` | All disciplines. Existing kind, reused. |

### Predicates

| Predicate | Subject | Object | Meaning |
| :--- | :--- | :--- | :--- |
| `prerequisiteOf` | discipline | discipline | Subject must precede object — the structural skeleton |
| `childOf` | discipline | discipline | Subject is part-of object — composition / zoom hierarchy (existing) |
| `hasReadiness` | discipline | NULL | Placeholder; readiness data carried in qualifiers |

`prerequisiteOf` and `hasReadiness` are new; `add_triple` auto-creates predicate rows on first use (or insert explicitly). `childOf` already has 3,370 uses.

### Qualifier keys (on `hasReadiness` triples)

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `value` | float as string | Unaided readiness in `[0.0, 1.0]` |
| `last_verified` | ISO 8601 string | Timestamp the readiness was last confirmed/probed |
| `delegation_recent` | `"true"` / `"false"` | Rob recently chose AI for this discipline (perishable; decays) |
| `base_value` | float as string | Leaky-integrator base — used by Phase 2 readiness engine |
| `tier` | string | Discipline tier — used by Phase 2 for half-life lookup |

Half-lives and floors for the leaky integrator live in a config file (sibling of `decay_policy.toml`), not as qualifiers — same convention as the Decay Policy.

## Worked example

A simple programming subgraph:

```text
(JavaScript, hasReadiness, NULL)
  qualifiers: value="0.65", last_verified="2026-05-15T10:00:00", delegation_recent="true"

(JavaScript, prerequisiteOf, React)
(JavaScript, prerequisiteOf, Node.js)
(React, prerequisiteOf, Next.js)

(JavaScript, childOf, Programming)
(React, childOf, Programming)
(Node.js, childOf, Programming)
(Next.js, childOf, Programming)
```

The frontier engine reads this snapshot and finds React: it's one prereq-step from JavaScript (which is on the readiness front at 0.65), so React surfaces as a frontier node when its own readiness is missing or stale.

## Write capability — verified

`phdb.triples.add_triple` accepts a `qualifiers: list[dict[str, str]]` parameter and reifies it into the `qualifiers` table via `_attach_qualifiers`. No additive migration needed. The vault-mcp `add_triple` MCP tool passes the parameter through to `phdb.triples.add_triple`. Cross-repo write path is in place.

## Out of scope (deferred)

- **Action edges** — generated at frontier time, not persisted as triples in V1. May be persisted as triples in Phase 6 for P(action) learning, with predicates like `wasRecommended` / `wasActedOn` — not designed here.
- **Line-level / per-commit provenance** of programming practice events — handled by the *AI-Authorship Provenance in Git* task (vault: `Outputs/Tasks/`), a hard dependency of the Phase 5 pilot.
- **Per-discipline practice-event taxonomy** for non-code disciplines — deferred to Skill Graph Phase 7.

## Modules

The skill-graph code lives in `src/phdb/skill_graph/`:

| Module | Phase | Purpose |
| :--- | :--- | :--- |
| `vocabulary.py` | 1 | Predicate names, qualifier keys, kind constants — the source of truth this doc encodes. |
| `readiness.py` | 2 | Leaky integrator over discipline readiness; atrophy alarm; config loader. |
| `practice_events.py` | 2 | Pull rob-authored commits from git, provenance-filtered via D7 (`phdb.authorship`). |
| `persistence.py` | 2 | Read/write readiness state to the predicate table (upsert qualifiers). |
| `engine.py` | 2 | Orchestrator — `update_discipline_readiness` end-to-end. |
| `models.py` | 3 | Dataclasses for in-memory graph representation. |
| `frontier.py` | 3 | Compute 1-hop frontier from a snapshot. |
| `edges.py` | 3 | Action-edge generation with an injectable provider. |
| `ranker.py` | 3 | Score `(value − cost + novelty) × P(action)`. |
| `digest.py` | 4 | Markdown digest of the frontier + ranked actions. |
| `canvas.py` | 4 | JSON Canvas renderer for the curated subgraph. |

Phase 3 and 4 modules are **pure functions over the dataclass models**. Phase 2 modules introduce I/O: `persistence` writes to the predicate table, `practice_events` shells out to git and queries `phdb.authorship`, and `engine` wires them together. Phase 5 will assemble Phase 2's output into snapshots consumed by Phase 3.

Tunable parameters for the readiness engine live in `config/skill_graph.toml` (sibling of `decay_policy.toml`) — per-tier half-lives, boost fraction, atrophy alarm threshold, discipline-to-tier mapping. Edit the toml; no code changes needed.
