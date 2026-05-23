-- Migration 0026 — Seed predicates for WPEF follow-on bookmark triples
-- Created: 2026-05-23
--
-- Phase 7 of the phdb Plugin Architecture plan — write-time triple
-- emission for bookmark ↔ web_page relationships (brief 100).
--
-- Three of the four predicates already exist from migration 0012:
--   - taggedWith  (0012)
--   - mentions    (0012)
--   - relatesTo   (0012, symmetric)
--
-- Only ``inFolder`` is new — it ranks alongside ``taggedWith`` as a
-- knowledge-tier organizing edge (bookmark belongs to a folder). Tier
-- chosen to align with the WPEF emission set, which is plugin-derived
-- but represents human-curated grouping (Raindrop folders, future
-- Chrome/Firefox folder trees).
--
-- Rollback: DELETE FROM predicates WHERE name = 'inFolder';

INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES (
        'inFolder',
        'knowledge',
        'Bookmark belongs to a folder/collection (Raindrop folder, browser bookmark folder).'
    );

INSERT OR IGNORE INTO schema_migrations(migration_id)
    VALUES ('0026_bookmark_triple_predicates');
