# Adapters Reference

34 adapters ship with personal-history-db. Each adapter transforms a specific export format into the unified schema.

## Quick reference

| `--adapter` | Source | Input format | Notes |
|:---|:---|:---|:---|
| `amazon` | Amazon account data | Zip (CSVs + JSON) | Orders, cart, reviews, Audible, PrimeVideo, wishlist |
| `apple_dbs` | iPhone backup | SQLite directory | Calls, voicemail, Safari, notes (multi-handler) |
| `apple_health` | Apple Health | `Health_Export.zip` | Records, workouts, clinical; streaming parse for large files |
| `apple_notes_full` | Apple Notes | `NoteStore.sqlite` | Upgrades `apple_dbs` rows with full note bodies |
| `calendar` | Google Calendar | `.ics` file, zip, or directory | Per-calendar threads |
| `chat_logs` | AIM/Trillian/MSN/Yahoo | Directory of `.htm`/`.log`/`.txt` | Three format auto-detected |
| `claude_chat` | Claude (claude.ai) | JSON data export | conversations, memories, users, projects |
| `claude_code` | Claude Code | Session `.jsonl` files | Per-session ingest |
| `discord` | Discord | `package.zip` data request | Outbound-only (Discord limitation) |
| `facebook` | Facebook Messages | Takeout zip (HTML) | Identity-based direction inference |
| `facebook_connections` | Facebook Friends | Takeout zip (HTML) | Writes to `connections` table |
| `facebook_posts` | Facebook Posts | Takeout zip (HTML) | Wall posts, status updates |
| `facebook_residuals` | Facebook misc. | Takeout zip (HTML) | Comments, likes, joins, invites |
| `gemini_scribe` | Gemini (vault) | Markdown with frontmatter | Vault AI session files |
| `gemini_web` | Gemini (web) | Markdown landmark files | Share + app format |
| `goodreads` | Goodreads | CSV export | Library/reading history |
| `google_activity` | Google My Activity | Takeout HTML | Search, YouTube, app activity |
| `google_contacts` | Google Contacts | Takeout `.vcf` | vCard parser (no external lib) |
| `google_drive` | Google Drive | Takeout zip or directory | Text extraction: docx, pdf, xlsx, html, etc. |
| `google_fit` | Google Fit | Takeout JSON | Activity segments + derived metrics |
| `google_timeline` | Google Timeline | `locationhistory.json` | Visits, activities, paths; post-2024 format |
| `google_voice` | Google Voice | Takeout HTML | Texts, voicemail, call records |
| `imessage` | iMessage | HTML export directory | From `imessage-exporter` tool |
| `mbox` | Gmail/email | `.mbox` file | Reference adapter; streaming multi-GB support |
| `onedrive` | OneDrive | Local directory | Selective body-extract by subdirectory |
| `phone_calls_xml` | Call logs | SMS Backup & Restore XML | Incoming/outgoing/missed/voicemail |
| `phone_photos` | Camera roll | Directory of image/video files | Date from filename patterns |
| `phone_photos_metadata` | Android MediaStore | `tar.gz` with SQLite | Photo metadata extraction |
| `phone_sms` | Android SMS/MMS | `mmssms.db` | Direct SQLite from TitaniumBackup |
| `raindrop` | Raindrop.io | CSV (also HTML, Session Buddy) | Writes to `bookmarks` table |
| `sms_xml` | SMS/MMS | SMS Backup & Restore XML | Falls back to lxml recovery parser |
| `spotify` | Spotify | Extended history JSON | Tracks, podcasts, audiobooks |
| `staged_md` | Generic markdown | Directory of `.md` with frontmatter | Catch-all adapter |
| `strong` | Strong (workout) | `Strong4.sqlite` | Core Data; exercise sets with weights |
| `titaniumbackup_browser_bookmarks` | Android Browser | TitaniumBackup `.tar.gz` | Writes to `bookmarks` table |
| `titaniumbackup_twitter` | Twitter/X | TitaniumBackup `.tar.gz` | Tweets, DMs, stories |

---

## Export instructions

How to obtain the source file for each adapter.

### Amazon

1. Go to [Request My Data](https://www.amazon.com/gp/privacycentral/dsar/preview.html)
2. Select all categories or specific ones (Order History, Digital Orders, etc.)
3. Download the zip when ready (usually 1-2 days)

### Apple Health

1. Open the Health app on iPhone
2. Tap your profile picture → Export All Health Data
3. Share/save the `Health_Export.zip`

### Apple Notes / Apple Databases

Requires a decrypted iPhone backup:

1. Create an encrypted iTunes/Finder backup
2. Decrypt with a tool like `iphone_backup_decrypt`
3. Point `apple_dbs` at the output directory
4. Run `apple_notes_full` after to upgrade note bodies

### Calendar

1. Google Takeout → select Google Calendar
2. Download; `.ics` files are under `Takeout/Calendar/`

### Claude (claude.ai)

1. Settings → Account → Export Data
2. Download arrives by email as JSON

### Claude Code

Session files are at `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`. Point the adapter at individual `.jsonl` files.

### Discord

1. Settings → Privacy & Safety → Request All of My Data
2. Download the `package.zip` (takes 1-30 days)

### Facebook (Messages / Posts / Connections / Residuals)

1. Settings → Your Information → Download Your Information
2. Format: **HTML** (not JSON)
3. Select: Messages, Posts, Friends, Activity Log
4. The same zip works for all four Facebook adapters

### Gemini

Vault files at `Timelines/AI Sessions/Gemini - *.md` (web) or files with `session_id` frontmatter (scribe).

### Goodreads

1. My Books → Import/Export → Export Library
2. Downloads a CSV

### Google (Activity / Contacts / Drive / Fit / Timeline / Voice)

All via [Google Takeout](https://takeout.google.com/):

| Adapter | Takeout selection |
|:---|:---|
| `google_activity` | My Activity |
| `google_contacts` | Contacts (vCard format) |
| `google_drive` | Drive |
| `google_fit` | Fit |
| `google_timeline` | Location History (JSON) |
| `google_voice` | Google Voice (HTML) |

### iMessage

1. Install [imessage-exporter](https://github.com/ReagentX/imessage-exporter)
2. Run: `imessage-exporter -f html -o ~/imessage-export/`
3. Point adapter at the output directory

### Gmail (mbox)

1. Google Takeout → select Mail
2. Download; `.mbox` file is under `Takeout/Mail/`

### Phone SMS/MMS/Calls

**XML format** (SMS Backup & Restore app):
1. Install [SMS Backup & Restore](https://play.google.com/store/apps/details?id=com.riteshsahu.SMSBackupRestore)
2. Export → creates XML files for SMS and calls

**SQLite format** (TitaniumBackup):
1. Locate `mmssms.db` in backup; extract from tarball if needed

### Raindrop.io

1. Settings → Data → Export
2. Choose CSV format

### Spotify

1. Account → Privacy Settings → Download Your Data
2. Request "Extended streaming history" (takes up to 30 days)
3. JSON files: `Streaming_History_Audio_*.json`

### Strong (workout app)

Requires iPhone backup with `Strong4.sqlite` in the decrypted backup tree.

---

## Adapter details

### Dedup strategies

Each adapter declares how duplicate detection works:

| Strategy | Mechanism | Used by |
|:---|:---|:---|
| `PLATFORM_SYNTHETIC` | Hash from stable platform-specific fields | Most adapters |
| `RFC822_MESSAGE_ID` | Uses Message-ID header or stable UUID as-is | `mbox`, `onedrive` |
| `CONTENT_HASH` | Hash of body text | `facebook_residuals`, `google_drive`, `phone_photos`, `titaniumbackup_browser_bookmarks` |

Re-ingesting the same source file is always safe — dedup ensures no duplicates.

### Special table targets

Most adapters write to the `messages` table. Exceptions:

| Adapter | Target table |
|:---|:---|
| `raindrop`, `titaniumbackup_browser_bookmarks` | `bookmarks` |
| `facebook_connections` | `connections` |
| `google_drive`, `onedrive`, `staged_md` (DigitalDocument types) | `documents` |
| `apple_health` | `messages` + sidecar tables (`record_metadata`, `hr_samples`, `workout_events`, `workout_statistics`, `geo_traces`) |

### Ordering constraints

- **`apple_notes_full` must run after `apple_dbs`** — it upgrades existing rows, doesn't create them
- All others are independent and can run in any order

### Gotchas

- **Discord exports are outbound-only** — Discord only includes your sent messages in data exports
- **Apple Health is huge** — uses streaming XML parse with memory management; do NOT try to load the full file
- **Google Timeline** — must use `run()` directly (`iter_rows()` raises NotImplementedError); post-2024 on-device format only
- **Facebook format must be HTML** — JSON format exports are not supported
- **Strong uses Z_PK for dedup** — not ZUNIQUEID (Core Data domain IDs can collide)
- **Phone SMS (SQLite)** — extract the `.db` from TitaniumBackup tarball before passing to adapter
- **Claude Code** — has a configurable path refusal list for legacy/dedup-clean locations
- **Google Drive** — text extraction degrades gracefully if `pypdf`/`python-docx`/`openpyxl` not installed (install via `pip install personal-history-db[docs]`)
