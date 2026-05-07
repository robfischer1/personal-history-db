---
created: 2026-05-06
status: phase-4-complete
type: project-state
related:
  - "[[REWRITE_PLAN]]"
  - "[[INVENTORY]]"
---

# Personal-History-DB — Port Log

Per-adapter port status for Phase 4. All 32 adapter modules ported and tested.

## Status legend

- `ported` — adapter created, tests passing, lint clean
- `instance` — belongs in instance tier, not project
- `n/a` — not an adapter (diagnostic/one-shot)

## Ports (32 adapters, all complete)

| # | Adapter | Legacy script(s) | Schema type(s) | Table target | Custom run()? | Status |
|--:|---|---|---|---|---|---|
| 1 | mbox | ingest_mbox.py | Message | messages | No | ported |
| 2 | imessage | ingest_imessage.py | Message | messages | No | ported |
| 3 | discord | ingest_discord.py | Message | messages | No | ported |
| 4 | chat_logs | ingest_chat_logs.py | Message | messages | No | ported |
| 5 | apple_dbs | ingest_apple_dbs.py | Message | messages | No | ported |
| 6 | sms_xml | ingest_sms_xml.py | Message | messages | No | ported |
| 7 | phone_calls_xml | ingest_phone_calls_xml.py | Action | messages | No | ported |
| 8 | spotify | ingest_spotify.py | ListenAction | messages | No | ported |
| 9 | goodreads | ingest_goodreads.py | ReadAction, Review | messages | No | ported |
| 10 | google_contacts | ingest_google_contacts.py | Person | messages | No | ported |
| 11 | strong | ingest_strong.py | ExerciseAction | messages | No | ported |
| 12 | amazon | ingest_amazon.py | OrderAction, Product, Review | messages | No | ported |
| 13 | calendar | ingest_calendar.py | Event | messages | No | ported |
| 14 | staged_md | ingest_staged_md.py | from @type frontmatter | messages | No | ported |
| 15 | google_fit | ingest_google_fit.py | ExerciseAction, Observation | messages | No | ported |
| 16 | google_voice | ingest_google_voice.py | Message, Action | messages | No | ported |
| 17 | google_activity | ingest_google_activity.py | 22+ stream-mapped types | messages | No | ported |
| 18 | facebook | ingest_facebook.py | Message | messages | No | ported |
| 19 | facebook_posts | ingest_facebook_posts.py | SocialMediaPosting | messages | No | ported |
| 20 | phone_sms | ingest_phone_sms.py | Message | messages | No | ported |
| 21 | apple_notes_full | ingest_apple_notes_full.py | DigitalDocument | messages | **Yes** (UPDATE) | ported |
| 22 | google_timeline | ingest_google_timeline.py | Place, TravelAction, GeoShape | messages + geo_traces | **Yes** | ported |
| 23 | google_drive | ingest_google_drive.py | DigitalDocument | messages | No | ported |
| 24 | onedrive | ingest_onedrive.py | DigitalDocument | messages | No | ported |
| 25 | phone_photos | ingest_phone_photos.py | Photograph | messages + attachments | No | ported |
| 26 | phone_photos_metadata | ingest_phone_photos_metadata.py | Photograph | messages | No | ported |
| 27 | facebook_residuals | ingest_facebook_residuals.py | Comment, LikeAction, etc. | messages | No | ported |
| 28 | titaniumbackup_twitter | ingest_titaniumbackup_twitter.py | SocialMediaPosting, Message | messages | No | ported |
| 29 | apple_health | ingest_apple_health.py | Observation, ExerciseAction, MedicalRecord | messages + 5 sidecars | **Yes** | ported |
| 30 | raindrop | ingest_raindrop.py | BookmarkAction | bookmarks | **Yes** | ported |
| 31 | titaniumbackup_browser_bookmarks | ingest_titaniumbackup_browser_bookmarks.py | BookmarkAction | bookmarks | **Yes** | ported |
| 32 | facebook_connections | ingest_facebook_connections.py | BefriendAction | connections | **Yes** | ported |

### Instance-only (not in project tier)

| Adapter | Reason |
|---|---|
| themes_scan | Rob-specific probe set; instance-private |

## Retirements (legacy scripts — no longer authoritative)

| File | Reason | Retired in commit |
|---|---|---|
| `ingest_meltext.py` | Explicit tombstone | — |
| `backfill_imessage_senders.py` | Folded into iMessage adapter | — |
| `backfill_phone_in_name.py` | Folded into iMessage adapter | — |
| `list_backup_files.py` | Diagnostic; not an ingester | — |
| `inspect_health_export.py` | Diagnostic; not an ingester | — |
| `verify_extracted.py` | Diagnostic | — |
| `wipe_apple_health.py` | One-shot destructive cleanup | — |

## Infrastructure ports (Phases 1, 5, 6 — not Phase 4)

| File | Phase | Status |
|---|---|---|
| `init_db.py` | Phase 1 (becomes `phdb migrate`) | done |
| `embed_messages.py` | Phase 6 | not started |
| `build_threads.py` | Phase 1 (base class handles) | done |
| `server.py` | Phase 5 (preserve MCP contract per `MCP-CONTRACT.md`) | not started |
| `retrieve.py` | Phase 5 (port → retire) | not started |

## Aggregate progress

- **Adapter ports:** 32 / 32 complete (100%)
- **Instance-only:** 1 (themes_scan — out of scope for project tier)
- **Infrastructure:** 2 / 5 complete (init_db + build_threads absorbed into framework)
