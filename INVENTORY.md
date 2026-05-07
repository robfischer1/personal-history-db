---
created: 2026-05-06
status: draft — awaits Rob's triage confirmation
type: project-inventory
related:
  - "[[REWRITE_PLAN]]"
  - "[[project_personal_history_db]]"
---

# Personal-History-DB — File Inventory

Snapshot of `System/Tools/personal-history-db/*.py` for Phase 4 triage planning. Each file is tagged with a recommended disposition based on what its docstring/header says. **Triage column is a recommendation; Rob confirms by changing the `?` to `✓` (or correcting the disposition).**

## Triage rubric

- **project** — generic enough to publish in the public project repo (formats, libraries, infrastructure)
- **instance** — tied to Rob's vault structure, hardcoded probe sets, or other personal-only logic — lives in instance-private adapters dir
- **retire** — deprecated, completed one-shot, diagnostic, superseded, or replaced by framework primitives

## Summary

- **Total files:** 46 (16,438 LOC)
- **Recommended port-as-project:** 36
- **Recommended port-as-instance:** 1
- **Recommended retire:** 9

---

## Infrastructure & framework

| File | LOC | Purpose | Triage | Confirmed |
|---|---:|---|---|:---:|
| `server.py` | 854 | MCP server exposing the 11 query tools | project | ? |
| `retrieve.py` | 395 | Hybrid retrieval (vec0 + FTS5 + RRF) | project → retire after Phase 5 | ? |
| `embed_messages.py` | 321 | Embeds non-bulk messages via local Ollama (`nomic-embed-text`) | project | ? |
| `build_threads.py` | 150 | Populates `threads` + `message_threads` from `gmail_thread_id` | project | ? |
| `init_db.py` | 98 | Applies numbered migrations in lexical order | project (becomes part of `phdb migrate`) | ? |

## Adapters — Apple ecosystem

| File | LOC | Purpose | Triage | Confirmed |
|---|---:|---|---|:---:|
| `ingest_apple_dbs.py` | 712 | iPhone backup SQLite dispatcher (13 sources) | project | ? |
| `ingest_apple_health.py` | 592 | Apple Health Export XML streaming | project | ? |
| `ingest_imessage.py` | 569 | imessage-exporter HTML (resumable, time-budgeted) | project | ? |
| `ingest_apple_notes_full.py` | 284 | Apple Notes proto parsing (2→3→2 path) | project | ? |
| `ingest_strong.py` | 290 | Strong workout SQLite (Z_PK dedup lesson) | project | ? |
| `decrypt_iphone_backup.py` | 207 | Wraps `iphone-backup-decrypt` library | project | ? |

## Adapters — Mail & messaging

| File | LOC | Purpose | Triage | Confirmed |
|---|---:|---|---|:---:|
| `ingest_chat_logs.py` | 822 | Legacy IM (AIM, MSN, Trillian, Yahoo) | project | ? |
| `ingest_mbox.py` | 559 | Gmail mbox (resumable, nohup-friendly) | project | ? |
| `ingest_discord.py` | 464 | Discord export `package.zip` | project | ? |
| `ingest_phone_sms.py` | 505 | Android `mmssms.db` (TitaniumBackup or standalone) | project | ? |
| `ingest_sms_xml.py` | 334 | SMS Backup & Restore SMS XML | project | ? |
| `ingest_phone_calls_xml.py` | 307 | SMS Backup & Restore call-log XML | project | ? |
| `ingest_google_voice.py` | 206 | Google Voice Takeout HTML | project | ? |

## Adapters — Facebook

| File | LOC | Purpose | Triage | Confirmed |
|---|---:|---|---|:---:|
| `ingest_facebook_connections.py` | 938 | FB connections graph (pluggable parser, migration 005) | project | ? |
| `ingest_facebook.py` | 416 | FB Messenger threads from FB export | project | ? |
| `ingest_facebook_residuals.py` | 396 | FB comments/reactions/groups/events/marketplace | project | ? |
| `ingest_facebook_posts.py` | 289 | FB posts | project | ? |

## Adapters — Google

| File | LOC | Purpose | Triage | Confirmed |
|---|---:|---|---|:---:|
| `ingest_google_activity.py` | 435 | Google Takeout MyActivity HTML (multi-stream) | project | ? |
| `ingest_google_drive.py` | 418 | Google Takeout Drive paths | project | ? |
| `ingest_google_timeline.py` | 307 | Google Timeline JSON (post-2024 on-device format) | project | ? |
| `ingest_google_contacts.py` | 221 | Google Contacts | project | ? |
| `ingest_google_fit.py` | 217 | Google Fit | project | ? |

## Adapters — Other sources

| File | LOC | Purpose | Triage | Confirmed |
|---|---:|---|---|:---:|
| `ingest_raindrop.py` | 986 | Raindrop bookmarks (single-table, normalized_url+instrument) | project | ? |
| `ingest_titaniumbackup_twitter.py` | 449 | Twitter Android per-account SQLite (TB2012, TB2013) | project | ? |
| `ingest_calendar.py` | 348 | iCal exports (`.ics` files or `.zip` bundles) | project | ? |
| `ingest_phone_photos_metadata.py` | 319 | Android MediaStore SQLite (metadata only) | project | ? |
| `ingest_phone_photos.py` | 308 | Camera roll photos and short videos | project | ? |
| `ingest_spotify.py` | 283 | Spotify Extended Streaming History | project | ? |
| `ingest_amazon.py` | 242 | Amazon "Request Your Data" zip | project | ? |
| `ingest_titaniumbackup_browser_bookmarks.py` | 231 | Android stock browser bookmarks + history | project | ? |
| `ingest_goodreads.py` | 180 | Goodreads CSV export | project | ? |
| `ingest_onedrive.py` | 425 | OneDrive local-FS (reuses google_drive text extraction) | project | ? |

## Generic infrastructure adapters

| File | LOC | Purpose | Triage | Confirmed |
|---|---:|---|---|:---:|
| `ingest_staged_md.py` | 339 | Generic staged-markdown ingester (frontmatter-driven) | project (likely retires once new framework's loader subsumes its job) | ? |

## Instance-private (Rob-specific logic)

| File | LOC | Purpose | Triage | Confirmed |
|---|---:|---|---|:---:|
| `themes_scan.py` | 228 | Runs curated probe set through retriever, writes to vault Garden/Brain Soup | instance | ? |

## Retire candidates

| File | LOC | Reason | Triage | Confirmed |
|---|---:|---|---|:---:|
| `ingest_meltext.py` | 17 | Explicit tombstone — file documents the decision not to ingest as per-message rows | retire | ? |
| `backfill_imessage_senders.py` | 155 | One-shot backfill — should be incorporated into the iMessage adapter's mapping logic | retire | ? |
| `backfill_phone_in_name.py` | 63 | One-shot backfill — same as above | retire | ? |
| `list_backup_files.py` | 156 | Diagnostic — finds paths for `decrypt_iphone_backup.py` failures | retire (or fold into `decrypt_iphone_backup.py` --diagnose) | ? |
| `inspect_health_export.py` | 142 | Diagnostic — inspects health export zip without extracting | retire (or fold into `ingest_apple_health.py` --inspect) | ? |
| `verify_extracted.py` | 119 | Diagnostic verification of extraction | retire | ? |
| `wipe_apple_health.py` | 142 | Destructive cleanup utility | retire (one-shot) | ? |

---

## Notes & flags

### Files that say "OPUS REVIEW NEEDED" in their headers
- `ingest_onedrive.py`
- `ingest_raindrop.py`

These flagged themselves before first production run. Worth surfacing as Phase 4 triage moments — design the port carefully rather than mechanical Gemini delegation.

### Files with subtle dedup logic (Adapter Architect attention warranted)
- `ingest_apple_dbs.py` — Strong Z_PK lesson originated here; documented in memory
- `ingest_apple_notes_full.py` — proto path 2→3→2 (was 2→2→1 in original docstring); fixed 2026-05-06
- `ingest_apple_health.py` — Core Data ZCREATIONDATE3 column-drift fix
- `ingest_phone_sms.py` — MMS extension added 2026-05-06; modes A and B for source format

### "Sandbox-safe" annotation
Several files note "this script is shipped for Rob to run locally on his D: drive." That's a runtime placement note (Cowork can't run them due to sandbox limits), not a privacy classification. Doesn't change triage.

### Generic-vs-instance boundary
The recommendation tilts heavily project-tier because the script *logic* is generic — it parses standard export formats. The Rob-specificity lives in:
- File paths (D:\<archives>\..., D:\50 Media\...)
- Identity normalization (Rob's emails, phone numbers, contact aliases)
- Atom @type customizations
- Tags Glossary closed vocabulary

Those values are extracted into instance config in Phase 3, leaving the adapter code generic.

### Files that may collapse during port
- `ingest_facebook.py` + `ingest_facebook_posts.py` + `ingest_facebook_residuals.py` + `ingest_facebook_connections.py` could become a single `facebook` adapter family with a sub-dispatcher (or stay split). Triage decision deferred to Phase 4.
- `ingest_phone_photos.py` + `ingest_phone_photos_metadata.py` could merge similarly.
- `ingest_sms_xml.py` + `ingest_phone_calls_xml.py` could share a SMS-Backup-Restore base.

### Backfills as adapter mappings
The two backfill scripts (`backfill_imessage_senders.py`, `backfill_phone_in_name.py`) exist because the original iMessage adapter didn't resolve sender contacts inline. Phase 4 port should fold their logic into the new iMessage adapter's mapping, then retire the standalone backfill scripts.

---

## Confirmation workflow

When Rob is ready to confirm triage:
1. Walk top-to-bottom; change `?` to `✓` for each agreed disposition
2. Edit any rows where the recommendation should change (e.g., `project` → `instance` or vice versa)
3. Save updates to `PORT_LOG.md` once confirmed (per-adapter port status separate from this static inventory)

Total to confirm: 46 rows.
