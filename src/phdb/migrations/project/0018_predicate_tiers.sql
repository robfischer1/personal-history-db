-- Migration 0018 — Predicate tier vocabulary + new relationship predicates
-- Created: 2026-05-22
--
-- Phase 0 of Messages Decomposition: adds the 4-tier classification system
-- to predicates (system/derived/knowledge/rob) and seeds predicates needed
-- for the messages→typed-tables migration (threading, recipients, sidecars,
-- chunks, attachments).
--
-- Rollback: DROP the tier column (SQLite requires table rebuild); DELETE new predicates.

-- 1. Add tier column — defaults to 'knowledge' (safe default for AI-curated triples)
ALTER TABLE predicates ADD COLUMN tier TEXT NOT NULL DEFAULT 'knowledge';

-- 2. Classify existing predicates per the 4-tier model
-- rob tier: Rob-exclusive, ingesters must not touch
UPDATE predicates SET tier = 'rob' WHERE name IN ('partOf', 'hasPart', 'wantsTo', 'outOf', 'wentTo');

-- derived tier: re-derivable from source data
UPDATE predicates SET tier = 'derived' WHERE name IN ('locatedAt', 'occurredAt', 'prev', 'next');

-- knowledge tier: AI/human curated (already the default, but explicit for clarity)
-- childOf, parentOf, relatesTo, mentions, mentionedBy, taggedWith, authoredBy,
-- authored, derivedFrom, supersedes, supersededBy, instanceOf, depicts, depictedIn, createdOn
-- These stay at the default 'knowledge' — no UPDATE needed.

-- 3. Seed new predicates for the messages decomposition

-- Threading: inThread / threadContains (derived — computed from message_threads FK)
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('inThread', 'derived', 'Message belongs to a conversation thread');
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('threadContains', 'derived', 'Thread contains this message — inverse of inThread');
UPDATE predicates SET inverse_predicate_id = (SELECT id FROM predicates WHERE name = 'threadContains')
    WHERE name = 'inThread' AND inverse_predicate_id IS NULL;
UPDATE predicates SET inverse_predicate_id = (SELECT id FROM predicates WHERE name = 'inThread')
    WHERE name = 'threadContains' AND inverse_predicate_id IS NULL;

-- Recipients: sentTo / receivedFrom (derived — computed from recipients FK)
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('sentTo', 'derived', 'Message/email was sent to this person/address');
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('receivedFrom', 'derived', 'Message/email was received from this person/address — inverse of sentTo');
UPDATE predicates SET inverse_predicate_id = (SELECT id FROM predicates WHERE name = 'receivedFrom')
    WHERE name = 'sentTo' AND inverse_predicate_id IS NULL;
UPDATE predicates SET inverse_predicate_id = (SELECT id FROM predicates WHERE name = 'sentTo')
    WHERE name = 'receivedFrom' AND inverse_predicate_id IS NULL;

-- Chunk ownership: hasChunk (system — structural, never curated)
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('hasChunk', 'system', 'Parent row owns this embedding chunk');

-- Attachment ownership: hasAttachment (system)
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('hasAttachment', 'system', 'Email/message has this file attachment');

-- Health sidecar ownership (system — structural links from observations to sidecar rows)
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('hasHeartRateSample', 'system', 'Health observation owns this heart rate sample');
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('hasGeoTrace', 'system', 'Health observation owns this geo trace point');
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('hasMetadata', 'system', 'Health observation owns this metadata record');
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('hasWorkoutEvent', 'system', 'Exercise action owns this workout event');
INSERT OR IGNORE INTO predicates (name, tier, description)
    VALUES ('hasWorkoutStatistic', 'system', 'Exercise action owns this workout statistic');

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0018_predicate_tiers');
