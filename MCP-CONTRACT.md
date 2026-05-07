---
created: 2026-05-06
status: snapshot — pre-rewrite contract reference
type: project-reference
related:
  - "[[REWRITE_PLAN]]"
  - "[[CURRENT-SCHEMA]]"
  - "[[project_personal_history_mcp]]"
---

# Personal-History-DB MCP — Tool Contract Snapshot

Complete enumeration of the 11 MCP tools exposed by `server.py`. **This is the behavioral preservation target for Phase 5** — the new query module + thin MCP adapter must produce identical responses to identical inputs.

## Server identity

- **MCP server name:** `personal-history-db`
- **Transport:** stdio (FastMCP default)
- **Entry point:** `server.py main()` → `mcp.run()`
- **Source file:** `System/Tools/personal-history-db/server.py` (854 LOC)

## Configuration via environment

| Env var | Default | Purpose |
|---|---|---|
| `PERSONAL_HISTORY_DB` | `<server.py grandparent>/personal-history.db` | Path to the SQLite DB |
| `OLLAMA_URL` | `http://localhost:11434` | Embedding endpoint |
| `OLLAMA_MODEL` | `nomic-embed-text` | Embedding model name |
| `DEFAULT_SINCE` | `2018` | Default lower date bound for `search` in hybrid mode (suppresses 2003 forum-corpus skew) |

## Constants

| Constant | Value | Purpose |
|---|---|---|
| `VECTOR_DIM` | `768` | nomic-embed-text dimension |
| `RRF_K` | `60` | Reciprocal-rank-fusion constant |
| `DATE_FILTER_OVERSAMPLE` | `6` | Multiplier for over-fetching when date filter active |
| `FTS_STOPWORDS` | (set of ~70 common English words) | Stripped from FTS queries |

## Connection behavior

- Single cached connection per process (`_conn` global)
- Connection sets `PRAGMA busy_timeout = 30000` to cooperate with concurrent ingesters
- `sqlite_vec.load(conn)` called at connection setup; vec0 extension required
- `row_factory = sqlite3.Row` for dict-like row access
- `check_same_thread=False` — connection is shared across MCP request threads

---

## Tool 1 — `search`

**Purpose:** Hybrid retrieval over the corpus. Combines vec0 semantic search with FTS5 keyword search, fuses with reciprocal-rank fusion (RRF).

### Parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | `str` | required | Natural-language query |
| `k` | `int` | `10` | Number of fused results |
| `since` | `str \| None` | `None` | Lower date bound — `"YYYY"`, `"YYYY-MM"`, `"YYYY-MM-DD"`. **In hybrid mode**, `None` defaults to `DEFAULT_SINCE` (2018). Pass `"1900"` to disable. |
| `until` | `str \| None` | `None` | Upper date bound, same format |
| `mode` | `str` | `"hybrid"` | `"hybrid"`, `"semantic"`, or `"fts"` |
| `include_bulk` | `bool` | `False` | If `False`, filters out `is_bulk=1` messages |

### Return shape

```json
{
  "query": str,
  "mode": str,
  "since": str,
  "until": str | null,
  "fts_mode": str,         // "AND (N hits)" | "OR (N hits)" | "n/a"
  "n_semantic": int,
  "n_fts": int,
  "results": [
    {
      "doc_id": int,
      "source_table": str,
      "source_id": int,
      "schema_type": str,
      "title": str | null,
      "chunk_index": int,
      "snippet": str,         // first 240 chars of content, newlines collapsed
      "msg_id": int | null,
      "subject": str | null,
      "sender": str | null,    // sender_name or sender_address
      "sender_address": str | null,
      "date": str | null,      // YYYY-MM-DD substring of date_sent
      "direction": str | null,
      "thread_id": str | null,
      "is_bulk": bool | null,
      "score": float           // RRF score, rounded to 5 decimals
    },
    ...
  ]
}
```

### Behavior

- **Default-since branch:** if `since=None` and `mode="hybrid"`, applies `DEFAULT_SINCE`. Other modes do not auto-apply.
- **Per-source over-fetch:** internally fetches `max(k * 5, 50)` per ranking source before fusion
- **Date-filter over-fetch:** applies `DATE_FILTER_OVERSAMPLE × k` to compensate for post-filter shrinkage
- **FTS fallback:** runs AND-mode first; if hits < `max(5, k//4)`, retries with OR-mode; reports which mode succeeded in `fts_mode`
- **Bulk filter applies post-hydration** — non-message docs (no `is_bulk` field) are kept regardless

---

## Tool 2 — `get_message`

**Purpose:** Fetch a full message by `messages.id` with body and metadata.

### Parameters

| Param | Type | Default |
|---|---|---|
| `msg_id` | `int` | required |
| `include_recipients` | `bool` | `True` |
| `include_attachments` | `bool` | `True` |

### Return shape

```json
{
  "id": int,
  "schema_type": str,
  "rfc822_message_id": str | null,
  "gmail_thread_id": str | null,
  "gmail_labels": str | null,        // raw JSON string
  "subject": str | null,
  "sender_address": str | null,
  "sender_name": str | null,
  "sender_domain": str | null,
  "direction": str | null,
  "date_sent": str | null,
  "date_received": str | null,
  "body_text": str | null,
  "body_text_source": str | null,
  "is_multipart": int,
  "has_attachments": int,
  "attachment_count": int,
  "is_bulk": int,
  "bulk_signal": str | null,
  "source_file_id": int | null,
  "recipients": [{"address", "name", "rtype"}, ...],     // if include_recipients
  "attachments": [{"filename", "content_type", "size_bytes", "on_disk_path"}, ...]  // if include_attachments AND attachment_count > 0
}
```

### Error shape

```json
{"error": "No message with id={msg_id}"}
```

---

## Tool 3 — `get_chunk`

**Purpose:** Fetch the full content of a document chunk by `documents.id`. Use after `search` returns a snippet you want to read in full.

### Parameters

| Param | Type | Default |
|---|---|---|
| `doc_id` | `int` | required |

### Return shape

```json
{
  "id": int,
  "schema_type": str,
  "source_table": str,
  "source_id": int,
  "chunk_index": int,
  "chunk_strategy": str | null,
  "title": str | null,
  "content": str,
  "metadata_json": str | null,        // raw JSON string
  "metadata": object | null,           // parsed if metadata_json is valid JSON; absent on parse failure
  "embedding_model": str | null,
  "embedded_at": str | null,
  "created_at": str
}
```

### Error shape

```json
{"error": "No document with id={doc_id}"}
```

---

## Tool 4 — `get_thread`

**Purpose:** Fetch all messages in a Gmail thread, ordered by date.

### Parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `thread_id` | `str \| None` | `None` | `gmail_thread_id` |
| `msg_id` | `int \| None` | `None` | If provided, resolves its thread |
| `max_messages` | `int` | `50` | LIMIT clause |

Provide either `thread_id` OR `msg_id`. Both null returns an error.

### Return shape

```json
{
  "thread_id": str,
  "message_count": int,
  "messages": [
    {
      "msg_id": int,
      "subject": str | null,
      "sender_address": str | null,
      "sender_name": str | null,
      "direction": str | null,
      "date_sent": str | null,
      "is_bulk": int,
      "body_preview": str | null     // substr(body_text, 1, 300)
    },
    ...
  ]
}
```

### Error shapes

```json
{"error": "provide thread_id or msg_id"}
{"error": "msg_id={msg_id} has no gmail_thread_id"}
```

### Behavior

- **Gmail-thread-only.** Doesn't yet route via the post-002 `(source_kind, thread_key)` identity. New query module should consider supporting both paths.

---

## Tool 5 — `list_sources`

**Purpose:** Inventory of what's been ingested. Counts grouped by source organization, file kind, and document source_table.

### Parameters

None.

### Return shape

```json
{
  "totals": {
    "messages": int,
    "documents": int,
    "vectors": int,
    "threads": int
  },
  "source_files": [
    {"source_org": str | null, "file_kind": str | null, "files": int, "messages": int | null},
    ...
  ],
  "documents_by_table": [
    {"source_table": str, "chunks": int, "embedded": int},
    ...
  ]
}
```

---

## Tool 6 — `corpus_stats`

**Purpose:** Year distribution + direction/sender breakdowns. Used to diagnose corpus skew.

### Parameters

| Param | Type | Default |
|---|---|---|
| `since` | `str \| None` | `None` |
| `until` | `str \| None` | `None` |

### Return shape

```json
{
  "since": str | null,
  "until": str | null,
  "by_year": [{"year": str, "messages": int}, ...],
  "by_direction": [{"direction": str | null, "n": int}, ...],
  "top_senders_nonbulk": [{"sender_address": str | null, "n": int}, ...]   // top 20, is_bulk=0
}
```

---

## Tool 7 — `nearest_neighbors`

**Purpose:** Find documents semantically similar to a given chunk. "More like this."

### Parameters

| Param | Type | Default |
|---|---|---|
| `doc_id` | `int` | required |
| `k` | `int` | `10` |

### Return shape

```json
{
  "seed_doc_id": int,
  "neighbors": [
    {
      // ...all fields from _hydrate output (same as search.results entries minus score)...
      "distance": float        // rounded to 5 decimals
    },
    ...
  ]
}
```

### Error shape

```json
{"error": "No vector for doc_id={doc_id} (not embedded?)"}
```

### Behavior

- Internally fetches `k+1` neighbors, drops the seed itself, returns up to `k`
- Uses vec0 `MATCH` syntax: `WHERE embedding MATCH ? AND k = ? ORDER BY distance`

---

## Tool 8 — `server_info`

**Purpose:** Diagnostic. DB location, size, Ollama reachability, embedded counts.

### Parameters

None.

### Return shape

```json
{
  "db_path": str,
  "db_exists": bool,
  "db_size_bytes": int | null,
  "ollama_url": str,
  "ollama_model": str,
  "default_since": str,
  "counts": {
    "messages": int,
    "documents": int,
    "vectors": int
  },
  "ollama_reachable": bool,
  "ollama_error": str | null,    // present if unreachable
  "db_error": str | null         // present if DB connection fails (replaces "counts")
}
```

### Behavior

- Probes Ollama via `GET /api/tags` with 3-second timeout
- On DB failure, returns `db_error` instead of `counts`

---

## Tool 9 — `find_messages_by_participant`

**Purpose:** Find messages where a specific person appears as sender, recipient, or either. Use when the question is about a person rather than a topic.

### Parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `participant` | `str` | required | Substring matched against sender_address, sender_name, recipients.address, recipients.name (lowercase, `LIKE %x%`) |
| `role` | `str` | `"any"` | `"sender"`, `"recipient"`, or `"any"` |
| `direction` | `str \| None` | `None` | Optional `messages.direction` filter |
| `since` | `str \| None` | `None` | |
| `until` | `str \| None` | `None` | |
| `limit` | `int` | `50` | |
| `include_bulk` | `bool` | `False` | |

### Return shape

```json
{
  "participant": str,
  "role": str,
  "since": str | null,
  "until": str | null,
  "match_count": int,
  "messages": [
    {
      "msg_id": int,
      "date": str | null,        // YYYY-MM-DD or null
      "direction": str | null,
      "sender": str | null,       // sender_name or sender_address
      "sender_address": str | null,
      "subject": str | null,
      "thread_id": str | null,    // gmail_thread_id
      "is_bulk": bool,
      "matched_via": str          // GROUP_CONCAT of "sender" and/or "recipient"
    },
    ...
  ]
}
```

### Error shape

```json
{"error": "role must be 'sender', 'recipient', or 'any'; got {role!r}"}
```

### Behavior

- Builds a UNION query across messages (for sender match) and recipients (for recipient match) per `role`
- Joins back to `messages` for filter columns
- Orders results by `date_sent DESC`

---

## Tool 10 — `find_threads`

**Purpose:** Find conversation threads by canonical subject line. Use when you remember a thread's title but not its contents.

### Parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | `str` | required | Substring matched against `threads.subject_canonical` (lowercase, `LIKE %x%`) |
| `since` | `str \| None` | `None` | Filters on `threads.date_last >= since` |
| `until` | `str \| None` | `None` | Filters on `threads.date_first <= until` |
| `limit` | `int` | `30` | |

### Return shape

```json
{
  "query": str,
  "since": str | null,
  "until": str | null,
  "match_count": int,
  "threads": [
    {
      "thread_db_id": int,
      "thread_id": str | null,    // gmail_thread_id
      "subject": str | null,
      "message_count": int,
      "date_first": str | null,
      "date_last": str | null,
      "participants": list | str | null    // parsed JSON array or raw string on parse failure
    },
    ...
  ]
}
```

### Behavior

- Orders by `date_last DESC`
- Tries to parse `participants` JSON; falls through to raw string on failure

---

## Tool 11 — `top_correspondents`

**Purpose:** Most-frequent correspondents in a date window.

### Parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `since` | `str \| None` | `None` | |
| `until` | `str \| None` | `None` | |
| `role` | `str` | `"sender"` | `"sender"`, `"recipient"`, or `"both"` |
| `limit` | `int` | `20` | |
| `exclude_bulk` | `bool` | `True` | Drop `is_bulk=1` |
| `exclude_self` | `bool` | `True` | Drop `direction='self'` (Discord/SMS noise) |

### Return shape

```json
{
  "role": str,
  "since": str | null,
  "until": str | null,
  "correspondents": [
    {"address": str | null, "name": str | null, "message_count": int},
    ...
  ]
}
```

### Error shape

```json
{"error": "role must be 'sender', 'recipient', or 'both'; got {role!r}"}
```

### Behavior

- `role="sender"` queries `messages` grouped by `sender_address`
- `role="recipient"` queries `recipients JOIN messages` grouped by `recipients.address`
- `role="both"` UNIONs both, groups across both, message_count is sum

---

## Supporting infrastructure (private helpers)

These functions are not MCP tools but the new query module must reproduce their semantics:

### `_embed_query(query: str) -> bytes`

- Sends `{"model": OLLAMA_MODEL, "input": [f"search_query: {query}"]}` to `${OLLAMA_URL}/api/embed`
- 30-second timeout
- Returns vec0-ready bytes via `struct.pack(f"{VECTOR_DIM}f", *embedding)`
- **Note the `search_query: ` prefix** — required by nomic-embed-text for asymmetric search

### `_semantic_search(conn, query_blob, k, since, until)`

- vec0 query: `WHERE embedding MATCH ? AND k = ? ORDER BY distance`
- Date-filter oversampling: fetches `k * DATE_FILTER_OVERSAMPLE` when filter active
- Returns `[(doc_id, distance, rank)]`

### `_build_fts_query(query, op="AND")`

- Strips `'` and `"` characters from query
- Splits on whitespace, keeps tokens that are alphanumeric AND not in `FTS_STOPWORDS`
- Joins with ` AND ` or ` OR `
- Returns `""` if no usable tokens

### `_fts_search(conn, query, k, since, until)`

- Tries AND query first
- If hits < `max(5, k//4)`, falls back to OR query
- Returns `(rows, mode_label)` where `mode_label` is `"AND (N hits)"`, `"OR (N hits)"`, or `"n/a"` if FTS5 unavailable

### `_rrf_fuse(*ranked_lists)`

- Reciprocal-rank fusion: `score[doc] = Σ 1/(RRF_K + rank)` across lists
- Returns sorted `[(doc_id, score)]` descending

### `_hydrate(conn, doc_ids, snippet_chars=240)`

- Joins `documents LEFT JOIN messages` on `m.id = d.source_id AND d.source_table = 'messages'`
- Non-message documents get null message-fields
- Snippet: first 240 chars of content with `\n` collapsed to spaces and stripped
- **Order preservation:** rebuilds output in original `doc_ids` order (important for ranking)

### `_date_filter_ids(conn, ids, since, until)`

- Filters a candidate doc_id set by `messages.date_sent` window
- Joins through `documents` for non-message docs (which the filter then excludes)

---

## Behavior preservation invariants for Phase 5

These must port exactly:

1. **Default-since branch only fires in hybrid mode** — semantic and fts modes leave `since=None` raw
2. **`include_bulk=False` filters post-hydration** — non-message docs are kept (their `is_bulk` is null)
3. **FTS AND→OR fallback threshold** is `max(5, k//4)`
4. **`search_query: ` prefix on Ollama input** — affects embedding output; cannot be dropped
5. **vec0 `MATCH` requires `k=?`** in the query (not `LIMIT`)
6. **`_hydrate` left-joins** — non-message docs survive
7. **`participants` JSON parse falls through to raw string** on failure; tests must check both shapes
8. **All return values are JSON-serializable plain dicts/lists** — no Pydantic model leakage at the MCP boundary
9. **Error responses are `{"error": ...}` shape** — never raise; always return a payload
10. **`schema_type` is preserved verbatim from DB** in `get_chunk`, `_hydrate`, `get_message`

## Behavior gaps the new query module should address

These are not contract violations to preserve — they're known weaknesses worth fixing during the rewrite (with backwards compatibility):

- **`get_thread` is Gmail-only.** Should support `(source_kind, thread_key)` per migration 002 and the new framework's threading.
- **`find_messages_by_participant` doesn't search across non-email sources via `recipients`** because non-email rows don't populate the `recipients` table consistently. Consider extending or adding a parallel lookup.
- **No tool exposes the `connections` table** (migration 005 — added 2026-05-06). Per `project_facebook_connections.md`, a `list_connections(status=, year=, name=)` tool was deferred. Worth adding in Phase 5 if the cost is low.
- **No tool exposes the `bookmarks` table.** Same pattern — `find_bookmarks(query=, instrument=, folder=)` would round out the surface.

---

## Verifying this snapshot

Before depending on this document for the rewrite, run each tool against the live MCP server and compare the response shape to the documented shape. Use a test harness like:

```python
import json, subprocess
# spawn server.py via stdio, send a JSON-RPC request, parse response
# compare keys / nested keys to expected shape
```

Any discrepancy is a snapshot bug to fix here before Phase 5 begins.
