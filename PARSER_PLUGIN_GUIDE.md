# Parser plugins for `ingest_facebook_connections.py`

_For: a future Claude session implementing the historical-backup parser._

The connections ingester (`ingest_facebook_connections.py`) is intentionally
parser-pluggable. The current takeout parser (`_FBTakeoutParser`) is the
working reference. A stub (`_FBHistoricalBackupParser`) is registered but
returns False from `detect()` — replace it once you have the historical
backup file in hand.

This guide explains the contract. Read it before adding code.

## Why this exists

Rob has at least one historical Facebook backup of unknown format (older than
the 2024+ takeout HTML). When that backup surfaces, you need to:

1. Know what shape the file is in
2. Write a parser that yields `ConnectionRow` records
3. Register it via `register(...)` next to the existing parsers

The reconciliation logic (status precedence, friends_since merging, inactive
inference) is already implemented and parser-agnostic. You only have to
produce well-formed `ConnectionRow` instances; the upsert handles the rest.

## The contract

A parser is any object that satisfies the `ParserPlugin` Protocol:

```python
@runtime_checkable
class ParserPlugin(Protocol):
    name: str               # unique identifier — written into source_files.source_kind
    description: str        # human-readable description
    def detect(self, path: Path) -> bool: ...
    def parse(self, path: Path) -> Iterator[ConnectionRow]: ...
```

`detect()` is called with each `--current` and `--historical` path; the FIRST
parser to return True wins. So make `detect()` narrow — only return True if
the file is unambiguously yours.

`parse()` yields `ConnectionRow` records:

```python
@dataclass
class ConnectionRow:
    instrument: str                       # 'facebook'
    display_name: str                     # raw name from the export
    connection_status: str                # see status table below
    source_file_label: str                # short identifier of the file within the export
    friends_since: str | None = None      # ISO date or None
    profile_url: str | None = None        # if extractable
    profile_id: str | None = None         # numeric ID
    vanity_slug: str | None = None
    raw_extra: dict = field(default_factory=dict)  # parser-specific extras for audit trail
```

## Status values you can yield

| Status              | Meaning                                                 |
|---------------------|---------------------------------------------------------|
| `active`            | Currently a friend at the time of this export           |
| `inactive`          | Was a friend, now removed (explicit unfriend record)    |
| `pending_outbound`  | Friend request sent, awaiting response                  |
| `pending_inbound`   | Friend request received, not yet acted on               |
| `rejected`          | Friend request rejected                                 |

If the historical file only has "current friends" with no nuance, just yield
all rows as `active`. Reconciliation will handle the rest:

- If a row was `active` in your historical export but missing from the latest
  current export, the post-pass flips it to `inactive` with reason
  `missing_from_latest_export`.
- If a row appears in BOTH and is `active` in current but had a friends_since
  in historical, the historical's date wins (earliest non-null).

## Identity / dedupe

Identity is computed downstream — you don't need to think about it. Just
yield the most-stable identity fields you can extract:

- `profile_url` if the export has anchor hrefs (older exports might).
- `profile_id` if you can pull a numeric FB ID out of `/profile.php?id=N`.
- `vanity_slug` if the URL is `/someslug`.
- `display_name` is mandatory (`ConnectionRow.__init__` requires it).

The downstream `make_dedupe_key()` prefers `profile_url`; falls back to
`name:<normalized-name>`. So providing a profile URL is gold (lets you
match across exports robustly), but missing one is OK.

## Implementing `detect()`

Make it narrow. If the historical backup is, say, a CSV with columns
`name,profile_id,added_on`, your detect should:

```python
def detect(self, path: Path) -> bool:
    if not (path.is_file() and path.suffix.lower() == ".csv"):
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:2]
    except OSError:
        return False
    if not head:
        return False
    cols = [c.strip().lower() for c in head[0].split(",")]
    return {"name", "profile_id"}.issubset(set(cols)) or {"name", "added_on"}.issubset(set(cols))
```

Do NOT return True for any zip / HTML that the FB takeout parser already
handles. The first match wins.

## Implementing `parse()`

Yield ConnectionRows, one per friend record. Example skeleton:

```python
def parse(self, path: Path) -> Iterator[ConnectionRow]:
    import csv
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            yield ConnectionRow(
                instrument="facebook",
                display_name=row["name"],
                connection_status="active",  # or whatever the column says
                source_file_label=path.name,
                friends_since=normalize_date(row.get("added_on")),
                profile_url=row.get("profile_url") or None,
                profile_id=row.get("profile_id") or None,
                raw_extra={"row": dict(row)},  # full row preserved for audit
            )
```

Date normalization: the existing parser uses `parse_fb_date()`. If your
format uses ISO already, pass it through; otherwise write a small parser.
Always emit ISO `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS`.

## Registration

After defining the class, register it:

```python
register(_HistoricalCSVParser())
```

Drop the registration line right after the class, and remove (or leave) the
`_FBHistoricalBackupParser` stub — it's harmless because its `detect()`
returns False.

## How to validate your parser

Before applying to the real DB:

1. Build a tiny scratch DB (mimic the test in `ingest_facebook_connections.py`
   review notes — minimal `schema_migrations` + `source_files` tables; the
   ingester runs the 005 migration itself).
2. Run with `--apply` against that scratch DB plus `--historical <path>`.
3. Inspect the rows:
   - Every row in your historical should now exist in the DB
   - Friends present in both historical and current should have current's
     status but possibly historical's friends_since
   - Friends only in historical should be inactive with
     `inactive_reason='missing_from_latest_export'`

Important: `--historical-id` should start with a YYYY[-MM[-DD]] prefix so
`derive_export_date()` can pull the date out. Otherwise mtime is used as a
fallback, which can fall forward to the current date and break reconciliation
precedence (the historical sighting will look newer than the current sighting
and the wrong status will win).

## Worked example: implementing the stub

Replace the body of `_FBHistoricalBackupParser` once you know the format:

```python
class _FBHistoricalBackupParser:
    name = "fb-historical-csv-2018"  # or whatever fits
    description = "Historical FB friends export — pre-2024 format. <Describe shape>."

    def detect(self, path: Path) -> bool:
        # narrow detection: filename or header check
        ...

    def parse(self, path: Path) -> Iterator[ConnectionRow]:
        # yield ConnectionRow per friend
        ...
```

Or — if you'd rather keep the stub class and add a NEW class for the
specific format — that's fine too; both will be registered and `find_parser`
just walks the list.

## What NOT to do

- Don't touch `upsert_connection`, `post_pass_infer_inactive`, or
  `write_review_report`. These are parser-agnostic. If a parser limitation
  forces a change there, surface it in conversation first.
- Don't add a new SQL migration for a new format. The schema is generic.
  Use `raw_extra` for parser-specific fields that don't fit; they're
  preserved in `appearances_json`.
- Don't widen the connection_status enum without amending the CHECK
  constraint in `005_connections.sql` AND adding a migration `006_*.sql`.
  Five values is enough for current FB needs; adding more is a bigger
  conversation.
- Don't write the parser to mutate state. `parse()` is a pure generator.

## Pointers to read first

- `ingest_facebook_connections.py` — the working FB takeout parser. Match
  its shape.
- `005_connections.sql` — table schema. Skim the comments at the top.
- `feedback_file_tool_truncation.md` (in user memory) — for any file
  you're growing past ~30 KB, use bash heredoc rather than Edit/Write,
  or you'll silently lose the tail.
