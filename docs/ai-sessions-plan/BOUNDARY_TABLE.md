---
created: 2026-05-07
updated: 2026-05-08
status: architecture-revised — Phase 0 <host> findings changed boundary approach
type: project-state
related:
  - "[[AI_SESSIONS_PLAN]]"
  - "[[INVENTORY]]"
---

# AI Sessions — Boundary Table

## Revised architecture (Phase 0 finding)

The original Phase 3a algorithm (derive boundaries from MyActivity.html `Prompted`/`Branched` cells) does not apply to the branch family because:

1. The Vault Structure Trunk + Branches happened **April 30, 2026**, after the April 28 takeout cutoff.
2. The 135 `Branched` cells in MyActivity.html are Gemini Scribe API calls from April 23, not web conversation forks.

**The landmark markdown files ARE the conversations.** Branch lineage is encoded directly via `"From Branch • ..."` body prefix strings. No MyActivity.html cross-reference is needed for the branch family.

---

## Branch family — source: landmark files

For these 4 conversations, the `gemini-web` adapter reads the landmark markdown files directly.

| branch_key | parent_thread_key | gemini_url | source | landmark_file | expected_msg_count | lineage_string |
| --- | --- | --- | --- | --- | ---: | --- |
| trunk | NULL | `gemini.google.com/share/b5681c2e0bb0` | landmark file | `Gemini - Obsidian Vault Structure and Metadata - Trunk.md` | (derive from ad-prompt/ad-ai-response block count) | (no prefix) |
| branch-1 | trunk | `gemini.google.com/app/b1d6038265f1505b` | landmark file | `Gemini - Obsidian Vault Structure and Metadata - Branch 1.md` | 114 | "From Obsidian Vault Structure and Metadata" |
| branch-2 | branch-1 | `gemini.google.com/app/96619097ab06c28f` | landmark file | `Gemini - Obsidian Vault Structure and Metadata - Branch 2.md` | 264 | "From Branch • Obsidian Vault Structure and Metadata" |
| branch-3 | branch-2 | `gemini.google.com/app/ab5501ea08d5b667` | landmark file | `Gemini - Obsidian Vault Structure and Metadata - Branch 3.md` | 306 | "From Branch • Branch • Obsidian Vault Structure and Metadata" |

**Lineage derivation rule:** Strip leading "From " → split on " • " → last element is the root conversation name; count of "Branch" tokens before the name is the depth. Depth 0 = trunk, depth 1 = branch-1, depth 2 = branch-2.

**Branch 1 / Trunk timestamp anomaly:** Branch 1's landmark first prompt is at 04:23 AM and Trunk's is at 4:46 PM on 2026-04-30. Branch 1 claims descent from Trunk. Possible: Trunk conversation started April 29 evening; Branch 1 forked early April 30 morning; the landmark captured a later re-entry timestamp for Trunk. Flag for Rob spot-check before Phase 3 ingest.

---

## Standalone conversations — source: landmark files + MyActivity.html confirmation

These 4 predate the takeout and have MyActivity.html `Prompted` cell matches (except #6).

| conv_key | gemini_url | landmark_file | myactivity_timestamp | myactivity_status |
| --- | --- | --- | --- | --- |
| curating-env | `gemini.google.com/share/7610047d3dfb` | `Gemini - Curating Your Environment.md` | Apr 17, 2026, 7:35:59 AM | ✓ matched |
| user-analysis | `gemini.google.com/share/d5e14f455187` | `Gemini - Gemini's User Analysis and Collaboration Offer.md` | (none — AI-initiated) | ✗ no Prompted cell |
| exporting-memories | `gemini.google.com/share/5f75b0649706` | `Gemini - Exporting Stored AI Memories and Context.md` | Apr 28, 2026, 12:14:36 AM | ✓ matched |
| cross-media | `gemini.google.com/share/4f1ab621723d` | `Gemini - Cross-Media Content Classification Systems.md` | Apr 26, 2026, 3:13:37 PM | ✓ matched |

---

## Un-landmarked conversations — source: MyActivity.html only

~292 `Prompted` cells in MyActivity.html that don't match any landmark file. Phase 3c generates a triage report. Rob decides per-conversation: stub-landmark / DB-only / drop.

Approximate breakdown by month:

- March 2026: 26 cells (icon generation, image requests, other)
- April 2026: ~266 cells (vault work, data analysis, other)

---

## Notes for Phase 3 adapter implementation

- The `gemini-web` adapter ingests landmark files as the primary input. MyActivity.html supplements timestamps for the 3 matched standalone conversations.
- For the branch family: parse `ad-prompt` / `ad-ai-response` blocks from each landmark file. One `messages` row per block, `role='user'` for ad-prompt, `role='assistant'` for ad-ai-response.
- For the branch family: the `parent_thread_key` is derived from the lineage string above, not from MyActivity.html.
- For un-landmarked conversations: MyActivity.html `Prompted` cells only — no full conversation content, just first-prompt text + timestamp.
- The `branch_fork` row for each branch is a synthetic marker row inserted between the parent thread's last message and the branch's first message.
