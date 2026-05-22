-- Migration 0016 — Create photographs typed table + seed photo predicates
-- Created: 2026-05-20
--
-- Fourth domain to get its own typed table (after messages, documents, articles).
-- First table born into the typed-table + predicates architecture — no parallel
-- messages row; the predicate graph provides the universal timeline index.
--
-- Receives rows whose schema_type='Photograph' — photo metadata from DigiKam,
-- phone_photos, or any future photo source. Columns are typed from EXIF/GPS/XMP.
--
-- Rollback: DROP TABLE photographs; DELETE FROM predicates WHERE name IN ('depicts','locatedAt','occurredAt');

CREATE TABLE IF NOT EXISTS photographs (
    id                INTEGER PRIMARY KEY,
    schema_type       TEXT NOT NULL DEFAULT 'Photograph',
    source_path       TEXT NOT NULL,        -- relative path within album root
    album_root        TEXT NOT NULL,         -- mount point / collection root
    content_hash      TEXT,                  -- DigiKam uniqueHash or similar
    captured_at       TEXT,                  -- ISO 8601 from EXIF DateTimeOriginal
    digitized_at      TEXT,                  -- ISO 8601 from EXIF DateTimeDigitized
    width             INTEGER,
    height            INTEGER,
    format            TEXT,                  -- jpeg, png, tiff, etc.
    file_size         INTEGER,
    camera_make       TEXT,                  -- EXIF Make
    camera_model      TEXT,                  -- EXIF Model
    lens              TEXT,                  -- EXIF LensModel
    focal_length      REAL,                  -- mm
    aperture          REAL,                  -- f-number
    exposure_time     REAL,                  -- seconds
    iso               INTEGER,              -- EXIF ISO speed
    latitude          REAL,                  -- decimal degrees
    longitude         REAL,                  -- decimal degrees
    altitude          REAL,                  -- meters above sea level
    rating            INTEGER,              -- 0-5 star rating
    source_org        TEXT NOT NULL DEFAULT 'digikam',
    source_kind       TEXT NOT NULL DEFAULT 'photo-metadata',
    provenance        TEXT NOT NULL,         -- e.g. "digikam:F:/Digikam/digikam4.db"
    raw_hash          TEXT,                  -- adapter dedup hash
    source_file_id    INTEGER REFERENCES source_files(id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_photographs_dedup
    ON photographs(source_file_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_photographs_hash
    ON photographs(content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_photographs_captured
    ON photographs(captured_at) WHERE captured_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_photographs_geo
    ON photographs(latitude, longitude) WHERE latitude IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_photographs_path
    ON photographs(album_root, source_path);

-- Seed predicates for photo-domain triples
INSERT OR IGNORE INTO predicates (name, description)
    VALUES ('depicts', 'Visual content — subject depicts the object (person, place, thing)');
INSERT OR IGNORE INTO predicates (name, inverse_predicate_id, description)
    VALUES ('depictedIn', NULL, 'Inverse of depicts — object appears in the subject image');
-- Wire up inverse pair
UPDATE predicates SET inverse_predicate_id = (SELECT id FROM predicates WHERE name = 'depictedIn')
    WHERE name = 'depicts' AND inverse_predicate_id IS NULL;
UPDATE predicates SET inverse_predicate_id = (SELECT id FROM predicates WHERE name = 'depicts')
    WHERE name = 'depictedIn' AND inverse_predicate_id IS NULL;

INSERT OR IGNORE INTO predicates (name, description)
    VALUES ('locatedAt', 'Spatial anchor — subject is located at / was taken at the object location');
INSERT OR IGNORE INTO predicates (name, description)
    VALUES ('occurredAt', 'Temporal anchor — subject occurred at the object timestamp');

-- Register migration
INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('0016_photographs');
