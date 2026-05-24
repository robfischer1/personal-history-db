-- Migration 0031 — Create 5 vault-entity tables for Entities/ named-object subdirs
-- Created: 2026-05-23
--
-- Ingests metadata from vault Entities/ subdirectories (People, Organizations,
-- Places, Software, Supplements) into DB-queryable typed tables. Unlike the
-- consumed-media dissolution (0030), these vault files are NOT deleted — they
-- remain in the vault with body content intact. The DB holds the structured
-- metadata for querying.
--
-- Table naming: `persons` / `places` / `products` already exist as action-shaped
-- message-decomposition tables. These new entity tables use distinct names to
-- avoid conflict. Phase 7 entity-factoring may merge them later.
--
-- Rollback: DROP TABLE people; DROP TABLE organizations; DROP TABLE entity_places;
--           DROP TABLE software_applications; DROP TABLE supplements;

-- ---- people (Person) --------------------------------------------------------
CREATE TABLE IF NOT EXISTS people (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'Person',
    additional_type TEXT,               -- e.g. "SocialContact"
    -- Schema.org Person
    name            TEXT NOT NULL,
    identifier      TEXT,
    email           TEXT,
    telephone       TEXT,
    address         TEXT,
    birth_date      TEXT,               -- ISO partial: --MM-DD or YYYY-MM-DD
    works_for       TEXT,               -- wikilink or org name
    url             TEXT,
    same_as         TEXT,               -- JSON array of profile URLs
    -- Operational
    tags            TEXT,               -- JSON array
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_people_dedup ON people(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_people_name ON people(name);

-- ---- organizations (Organization / Corporation / Periodical) ----------------
CREATE TABLE IF NOT EXISTS organizations (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'Organization',
    additional_type TEXT,               -- e.g. "Company"
    -- Schema.org Organization
    name            TEXT NOT NULL,
    identifier      TEXT,
    url             TEXT,
    -- Operational
    tags            TEXT,               -- JSON array
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_dedup ON organizations(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_organizations_name ON organizations(name);

-- ---- entity_places (Store / Restaurant / HealthClub / etc.) -----------------
-- Named `entity_places` to avoid conflict with action-shaped `places` table.
CREATE TABLE IF NOT EXISTS entity_places (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'Place',
    -- Schema.org Place
    name            TEXT NOT NULL,
    identifier      TEXT,
    address         TEXT,
    geo             TEXT,               -- "lat, lon" string
    telephone       TEXT,
    url             TEXT,
    -- Operational
    tags            TEXT,               -- JSON array
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_places_dedup ON entity_places(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_entity_places_name ON entity_places(name);

-- ---- software_applications (SoftwareApplication) ----------------------------
CREATE TABLE IF NOT EXISTS software_applications (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'SoftwareApplication',
    -- Schema.org SoftwareApplication
    name            TEXT NOT NULL,
    identifier      TEXT,
    url             TEXT,
    categories      TEXT,               -- JSON array
    -- Operational
    tags            TEXT,               -- JSON array
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_software_applications_dedup ON software_applications(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_software_applications_name ON software_applications(name);

-- ---- supplements (DietarySupplement) ----------------------------------------
CREATE TABLE IF NOT EXISTS supplements (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'DietarySupplement',
    additional_type TEXT,               -- e.g. "Supplement"
    -- Schema.org Product / DietarySupplement
    name            TEXT NOT NULL,
    identifier      TEXT,
    description     TEXT,
    status          TEXT,               -- e.g. "Pending"
    categories      TEXT,               -- JSON array
    -- Operational
    tags            TEXT,               -- JSON array
    file_path       TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_supplements_dedup ON supplements(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_supplements_name ON supplements(name);

-- ---- Register migration -----------------------------------------------------
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0031_vault_entity_tables');
