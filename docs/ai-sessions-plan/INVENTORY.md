---
created: 2026-05-07
updated: 2026-05-08
status: complete — Phase 0 verified on <host> 2026-05-08
type: project-inventory
related:
  - "[[AI_SESSIONS_PLAN]]"
  - "[[project_personal_history_db]]"
---

# AI Sessions — Landmark Inventory

Catalog of vault landmark files for the `gemini-web` and `gemini-scribe` adapters. Match status against `MyActivity.html` requires <host> (file lives at `D:\<archives>\2026-04-28 Google Takeout\Takeout\My Activity\Gemini Apps\MyActivity.html`).

---

## Gemini-web landmark files (8 files)

These are the source-of-truth for Phase 3 boundary derivation. Columns:

- **url** — the Gemini conversation URL extracted from frontmatter
- **url_type** — `/app` = live conversation (has message count); `/share` = published snapshot (no count)
- **conv_date** — date/time of first prompt (from ad-prompt block or metadata)
- **vault_created** — landmark file creation date
- **msg_count** — from `description:` field; `n/a` if only "Created with Gemini"
- **branch_lineage** — the "From Branch • …" prefix in the file body (indicates parent)
- **myactivity_match** — whether a `Prompted` cell was found in `MyActivity.html`

### Branch family: Obsidian Vault Structure and Metadata

> **Phase 0 finding:** The entire branch family (Trunk + Branches 1/2/3) happened **April 30, 2026**, which is **after the April 28 takeout cutoff**. None of these conversations appear in `MyActivity.html`. The landmark markdown files are the authoritative source for these conversations. See §Phase 0 findings below.

| # | File | URL | url_type | conv_date | vault_created | msg_count | branch_lineage (→ parent) | myactivity_match |
| --: | --- | --- | :---: | --- | --- | ---: | --- | :---: |
| 1 | Gemini - Obsidian Vault Structure and Metadata - Trunk | `gemini.google.com/share/b5681c2e0bb0` | /share | 2026-04-30 16:46 | 2026-04-30 | n/a | (trunk — no prefix) | ✗ post-takeout |
| 2 | Gemini - Obsidian Vault Structure and Metadata - Branch 1 | `gemini.google.com/app/b1d6038265f1505b` | /app | 2026-04-30 04:23 AM | 2026-04-30 | 114 | "From Obsidian Vault Structure and Metadata" → Trunk | ✗ post-takeout |
| 3 | Gemini - Obsidian Vault Structure and Metadata - Branch 2 | `gemini.google.com/app/96619097ab06c28f` | /app | 2026-04-30 18:17 | 2026-04-30 | 264 | "From Branch • Obsidian Vault Structure and Metadata" → Branch 1 | ✗ post-takeout |
| 4 | Gemini - Obsidian Vault Structure and Metadata - Branch 3 | `gemini.google.com/app/ab5501ea08d5b667` | /app | 2026-04-30 09:03 AM | 2026-04-30 (updated 2026-05-01) | 306 | "From Branch • Branch • Obsidian Vault Structure and Metadata" → Branch 2 | ✗ post-takeout |

**Note on Branch 1 timestamp anomaly (still open):** Branch 1's first prompt is at 04:23 AM and Trunk's first prompt is at 4:46 PM on 2026-04-30. Branch 1 claims lineage from Trunk, but started earlier. Possible explanations: (a) Trunk was an ongoing conversation from the prior day and the landmark captured a mid-conversation re-entry; (b) the `/share` URL is a snapshot of a longer earlier conversation. Flag for Rob spot-check before Phase 3 ingest.

### Standalone conversations (no branch lineage)

These 4 conversations all predate the April 28 takeout and have `Prompted` cell matches in `MyActivity.html` (except file #6 which starts with an AI response).

| # | File | URL | conv_date | vault_created | first_prompt (excerpt) | myactivity_match | matched_cell_timestamp |
| --: | --- | --- | --- | --- | --- | :---: | --- |
| 5 | Gemini - Curating Your Environment | `gemini.google.com/share/7610047d3dfb` | 2026-04-17 07:35 | 2026-04-30 | "What's a positive pattern that stands out in my life lately?" | ✓ | Apr 17, 2026, 7:35:59 AM |
| 6 | Gemini - Gemini's User Analysis and Collaboration Offer | `gemini.google.com/share/d5e14f455187` | 2026-04-18 20:06 | 2026-04-30 | (starts with AI response — no user prompt first) | ✗ no Prompted cell | AI-initiated conversation |
| 7 | Gemini - Exporting Stored AI Memories and Context | `gemini.google.com/share/5f75b0649706` | 2026-04-28 00:14 | 2026-04-30 | "Export all of my stored memories and any context..." | ✓ | Apr 28, 2026, 12:14:36 AM |
| 8 | Gemini - Cross-Media Content Classification Systems | `gemini.google.com/share/4f1ab621723d` | 2026-04-26 15:13 | 2026-04-30 | "Is there a broad subject or genre classification system..." | ✓ | Apr 26, 2026, 3:13:37 PM |

---

## Gemini Scribe files (5 files)

These are `gemini-scribe` format — Scribe sessions with `session_id`, `enabled_tools`, and `accessed_files` in frontmatter. Each has structured `ad-prompt` / `ad-ai-response` blocks with per-prompt timestamps.

| # | File | session_id | created | last_active |
| --: | --- | --- | --- | --- |
| 1 | 2026-04-16 Writing Style Guide Analysis | session_1776393659260_mh61ecdro | 2026-04-16 | 2026-04-16T22:53:25-04:00 |
| 2 | 2026-04-17 Vault Organization Strategy | session_1776402555625_9pij4j7mm | 2026-04-17 | 2026-04-18T19:58:16-04:00 |
| 3 | 2026-04-23 Active Obsidian Plugins List | session_1776932261425_eni3gm5aw | 2026-04-23 | 2026-04-23T04:18:51-04:00 |
| 4 | 2026-04-23 Aligning Templates with Governance | session_1776973922317_rakzttr30 | 2026-04-23 | 2026-04-23T17:34:19-04:00 |
| 5 | 2026-04-25 Update SCHEMA.md with Schema.org keys | session_1777122926545_zg9rpqs71 | 2026-04-25 | 2026-04-25T09:39:16-04:00 |

Gemini Scribe session IDs will be used as `thread_key` values in Phase 4.

---

## Claude Code sessions

Sourced from `~\.claude\projects\<encoded-cwd>\<session-uuid>.jsonl`. Not vault-resident; inventory must be done on <host> by walking the `.claude/projects/` tree. Deferred to Phase 2 pre-flight.

---

## MyActivity.html outer-cell stats (verified on <host> 2026-05-08)

File: `D:\<archives>\2026-04-28 Google Takeout\Takeout\My Activity\Gemini Apps\MyActivity.html`
Size: 2,526,624 bytes — intact.

**Actual verb breakdown:**

| Verb | Count | Notes |
| --- | ---: | --- |
| Prompted | 295 | Prior Cowork estimate was 292; difference is parser-artifact |
| Branched | 135 | ALL from 2026-04-23 04:22 AM within a 6-second window — see finding #2 |
| Created | 5 | System event |
| Gave | 2 | System event |
| Answered | 1 | System event |
| Used | 1 | System event |
| Selected | 1 | System event |
| **Total** | **440** | ✓ matches plan exit criterion |

**Month distribution of Prompted cells:**

| Month | Count |
| --- | ---: |
| March 2026 | 26 |
| April 2026 | 269 |
| **Total** | **295** |

**Attachment files in Gemini Apps directory:**

| Type | Count |
| --- | ---: |
| Image attachments (.jpg/.png etc.) | 42 |
| Doc attachments (no extension, hash-suffixed) | 66 |
| MyActivity HTML files | 7 |
| Other (Amazon exports, search history, etc. Rob uploaded as Gemini context) | 22 |
| **Total files** | **137** |

Note: the plan estimated 130 hash-suffixed files; actual is 108 (42 + 66). The 22 "other" files include Amazon order history, YouTube watch history, and similar data Rob uploaded as Gemini conversation context — they have non-hash suffixes.

---

## Phase 0 findings — plan deviations

Two findings from the <host> verification significantly change Phase 3 architecture:

### Finding 1: Takeout cutoff — branch family not in MyActivity.html

The April 28, 2026 takeout captures Gemini activity through April 28 only. The Vault Structure conversation tree (Trunk + Branches 1/2/3) all occurred on **April 30, 2026** — after the takeout. None of these appear in `MyActivity.html`.

**Impact on Phase 3:** The boundary derivation algorithm (Phase 3a) cannot use MyActivity.html as the source for the branch family. The **landmark markdown files are the authoritative source** for these conversations — they contain the full content as `ad-prompt` / `ad-ai-response` blocks. The `gemini-web` adapter must parse the landmark files directly, not correlate them against MyActivity.html.

### Finding 2: "Branched" cells ≠ web conversation forks

All 135 `Branched` cells in MyActivity.html are from April 23, 2026 at 4:22 AM — all within a 6-second window. Their content is the Vault Structure conversation text. These are **Gemini Scribe API calls**: when the April 23 Scribe session ("Vault Organization Strategy") re-imported the prior Vault Structure conversation into a new Gemini API context, each message was logged as a `Branched` event. They do not represent user-initiated web conversation forks.

**Impact on Phase 3a:** The plan's boundary algorithm (match `Branched` cells → landmark fork events) does not apply. The branch lineage is encoded directly in the landmark files via their `"From Branch • ..."` body prefix strings — no MyActivity.html cross-referencing needed for the branch family.

### Revised Phase 3 architecture (from these findings)

| Source | Conversations | Approach |
| --- | --- | --- |
| Landmark markdown files | Trunk, Branch 1, Branch 2, Branch 3 | Parse ad-prompt/ad-ai-response blocks directly |
| Landmark markdown files | Curating Your Env., Exporting Memories, Cross-Media | Parse directly; MyActivity.html confirms timestamps only |
| MyActivity.html (no landmark) | Gemini's User Analysis (starts with AI response) | Parse landmark file; no MyActivity.html Prompted cell |
| MyActivity.html (un-landmarked) | ~292 other Prompted cells | Triage report → Rob decides per-conversation |

---

## Match status summary

| Category | Total | Matched | Not matched | Notes |
| --- | ---: | ---: | ---: | --- |
| Gemini-web branch family | 4 | 0 | 4 | Post-takeout; use landmark files directly |
| Gemini-web standalone | 4 | 3 | 1 | #6 (User Analysis) starts with AI response |
| Gemini Scribe | 5 | n/a | n/a | Scribe files not in MyActivity.html |
| **Total vault landmarks** | **13** | **3** | **5** | |
