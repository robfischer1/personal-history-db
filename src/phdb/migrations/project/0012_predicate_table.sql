-- Migration 0012 — predicate table (RDF-triple store)
-- Created: 2026-05-18
--
-- Adds four tables for a Wikidata-inspired typed-edge graph:
--   nodes       — addressable referents (vault files, concepts, people, dates, literals)
--   predicates  — controlled vocabulary of edge types (camelCase, invertible)
--   triples     — subject → predicate → object edges with timestamps and provenance
--   qualifiers  — reified key/value pairs attached to triples (Wikidata pattern)
--
-- Purely additive — no existing rows move or drop.
-- Phase 0 Q3 override: nodes link to phdb corpus rows via source_table + source_id.
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS qualifiers;
--   DROP TABLE IF EXISTS triples;
--   DROP TABLE IF EXISTS predicates;
--   DROP TABLE IF EXISTS nodes;

-- ============================================================================
-- 1. nodes — addressable referents
-- ============================================================================
CREATE TABLE IF NOT EXISTS nodes (
    id               INTEGER PRIMARY KEY,
    label            TEXT    NOT NULL,
    normalized_label TEXT    NOT NULL,
    kind             TEXT    NOT NULL,  -- file | concept | person | place | date | literal
    vault_path       TEXT,              -- canonical .md path when kind='file'
    source_table     TEXT,              -- phdb corpus table (messages | documents | ...)
    source_id        INTEGER,           -- row ID in source_table
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_dedup
    ON nodes(kind, normalized_label);

CREATE INDEX IF NOT EXISTS idx_nodes_vault_path
    ON nodes(vault_path) WHERE vault_path IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_nodes_corpus
    ON nodes(source_table, source_id)
    WHERE source_table IS NOT NULL;

-- ============================================================================
-- 2. predicates — controlled vocabulary of edge types
-- ============================================================================
CREATE TABLE IF NOT EXISTS predicates (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL UNIQUE,  -- camelCase: childOf, relatesTo
    inverse_predicate_id INTEGER REFERENCES predicates(id),
    symmetric            INTEGER NOT NULL DEFAULT 0,
    description          TEXT
);

-- ============================================================================
-- 3. triples — subject → predicate → object edges
-- ============================================================================
CREATE TABLE IF NOT EXISTS triples (
    id              INTEGER PRIMARY KEY,
    subject_node_id INTEGER NOT NULL REFERENCES nodes(id),
    predicate_id    INTEGER NOT NULL REFERENCES predicates(id),
    object_node_id  INTEGER REFERENCES nodes(id),  -- NULLABLE: open/incomplete triple
    observed_at     TEXT,                           -- event time (when the relationship held)
    provenance      TEXT    NOT NULL,               -- extraction | explicit | ai-emitted
    source_ref      TEXT,                           -- file path or session id
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_triples_subject
    ON triples(subject_node_id);

CREATE INDEX IF NOT EXISTS idx_triples_predicate
    ON triples(predicate_id);

CREATE INDEX IF NOT EXISTS idx_triples_object
    ON triples(object_node_id)
    WHERE object_node_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_triples_observed
    ON triples(observed_at)
    WHERE observed_at IS NOT NULL;

-- Dedup index: COALESCE sentinels handle NULL uniqueness in SQLite
CREATE UNIQUE INDEX IF NOT EXISTS idx_triples_dedup
    ON triples(
        subject_node_id,
        predicate_id,
        COALESCE(object_node_id, -1),
        provenance,
        COALESCE(source_ref, '')
    );

-- ============================================================================
-- 4. qualifiers — reified key/value pairs on triples
-- ============================================================================
CREATE TABLE IF NOT EXISTS qualifiers (
    id        INTEGER PRIMARY KEY,
    triple_id INTEGER NOT NULL REFERENCES triples(id) ON DELETE CASCADE,
    key       TEXT    NOT NULL,  -- bucket | context | valid_from | valid_to | confidence
    value     TEXT
);

CREATE INDEX IF NOT EXISTS idx_qualifiers_triple
    ON qualifiers(triple_id);

-- ============================================================================
-- 5. Seed predicates — controlled vocabulary (~20 entries)
-- ============================================================================

-- Asymmetric pairs: insert both directions, then wire inverse_predicate_id
INSERT OR IGNORE INTO predicates (id, name, symmetric, description) VALUES
    ( 1, 'childOf',      0, 'Hierarchical parent — from up: frontmatter'),
    ( 2, 'parentOf',     0, 'Hierarchical child — inverse of childOf'),
    ( 3, 'relatesTo',    1, 'General association — from links: frontmatter'),
    ( 4, 'mentions',     0, 'References a named entity — from keywords: frontmatter'),
    ( 5, 'mentionedBy',  0, 'Referenced by another note — inverse of mentions'),
    ( 6, 'taggedWith',   0, 'Labeled with a tag — from tags: frontmatter'),
    ( 7, 'authoredBy',   0, 'Written or created by a person'),
    ( 8, 'authored',     0, 'Person wrote or created this — inverse of authoredBy'),
    ( 9, 'partOf',       0, 'Component membership — note is part of a larger whole'),
    (10, 'hasPart',      0, 'Contains a component — inverse of partOf'),
    (11, 'derivedFrom',  0, 'Provenance chain — this was derived from the object'),
    (12, 'supersedes',   0, 'Replaces or overrides the object'),
    (13, 'supersededBy', 0, 'Replaced or overridden by the object — inverse of supersedes'),
    (14, 'instanceOf',   0, 'Type classification — this is an instance of the object'),
    (15, 'createdOn',    0, 'Temporal anchor — subject was created on a date node'),
    (16, 'wantsTo',      0, 'Intentional state — subject wants to do/have the object'),
    (17, 'outOf',        0, 'Depletion state — subject is out of the object'),
    (18, 'wentTo',       0, 'Spatial/event — subject went to the object location/event'),
    (19, 'prev',         0, 'Chronological predecessor in a sequence'),
    (20, 'next',         0, 'Chronological successor — inverse of prev');

-- Wire inverse predicate pairs
UPDATE predicates SET inverse_predicate_id =  2 WHERE id =  1;  -- childOf ↔ parentOf
UPDATE predicates SET inverse_predicate_id =  1 WHERE id =  2;
UPDATE predicates SET inverse_predicate_id =  5 WHERE id =  4;  -- mentions ↔ mentionedBy
UPDATE predicates SET inverse_predicate_id =  4 WHERE id =  5;
UPDATE predicates SET inverse_predicate_id =  8 WHERE id =  7;  -- authoredBy ↔ authored
UPDATE predicates SET inverse_predicate_id =  7 WHERE id =  8;
UPDATE predicates SET inverse_predicate_id = 10 WHERE id =  9;  -- partOf ↔ hasPart
UPDATE predicates SET inverse_predicate_id =  9 WHERE id = 10;
UPDATE predicates SET inverse_predicate_id = 13 WHERE id = 12;  -- supersedes ↔ supersededBy
UPDATE predicates SET inverse_predicate_id = 12 WHERE id = 13;
UPDATE predicates SET inverse_predicate_id = 20 WHERE id = 19;  -- prev ↔ next
UPDATE predicates SET inverse_predicate_id = 19 WHERE id = 20;

-- relatesTo is its own inverse (symmetric)
UPDATE predicates SET inverse_predicate_id = 3 WHERE id = 3;

-- ============================================================================
-- 6. Record this migration as applied
-- ============================================================================
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0012_predicate_table');
