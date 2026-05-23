-- Migration 0029 — formalize facet_coalescence_log table
-- Created: 2026-05-23
--
-- Phase 8A of the phdb Plugin Architecture plan promotes the
-- opportunistic ensure_audit_log()-created table to a formal
-- migration. The audit table records every facet-coalescence merge
-- (people identity, places resolution); `phdb facet <name> unmerge
-- <id>` reads from it to reverse a merge.
--
-- Backward compatibility: phdb.facets.base.ensure_audit_log() is
-- preserved as a fallback path for legacy DBs that haven't run this
-- migration yet (e.g., DBs created before Phase 8 from running facet
-- pipelines at Phase 4). CREATE TABLE IF NOT EXISTS + CREATE INDEX
-- IF NOT EXISTS make the migration safe even when the table is
-- already present.

CREATE TABLE IF NOT EXISTS facet_coalescence_log (
    id              INTEGER PRIMARY KEY,
    facet_type      TEXT NOT NULL,
    facet_node_id   INTEGER NOT NULL,
    rule_name       TEXT,
    confidence      REAL,
    source_table    TEXT,
    source_id       INTEGER,
    payload         TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_facet_coalescence_log_facet
    ON facet_coalescence_log(facet_type, facet_node_id);

CREATE INDEX IF NOT EXISTS idx_facet_coalescence_log_created
    ON facet_coalescence_log(created_at);

CREATE INDEX IF NOT EXISTS idx_facet_coalescence_log_rule
    ON facet_coalescence_log(rule_name);

INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0029_facet_coalescence_log');
