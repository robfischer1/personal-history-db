-- Rollback for migration 0012 — predicate table
-- Drops all four tables and removes the migration record.
-- Manual insurance only — not run by the migration runner.

DROP TABLE IF EXISTS qualifiers;
DROP TABLE IF EXISTS triples;
DROP TABLE IF EXISTS predicates;
DROP TABLE IF EXISTS nodes;

DELETE FROM schema_migrations WHERE migration_id = '0012_predicate_table';
