-- @file: sql/08_omnichannel.sql
-- @exports: raw_reports.content_fingerprint
-- @depends_on: sql/01_schema.sql
-- @purpose: Cross-provider SHA-256 duplicate exclusion for new ingestion reports.
BEGIN;
ALTER TABLE raw_reports ADD COLUMN IF NOT EXISTS content_fingerprint TEXT;
ALTER TABLE raw_reports ADD COLUMN IF NOT EXISTS source_type TEXT;
ALTER TABLE raw_reports DROP CONSTRAINT IF EXISTS raw_reports_geocode_method_check;
ALTER TABLE raw_reports ADD CONSTRAINT raw_reports_geocode_method_check
    CHECK (geocode_method IN ('gazetteer','geoapify','nominatim','provided','unresolved'));
CREATE UNIQUE INDEX IF NOT EXISTS raw_reports_fingerprint_idx
    ON raw_reports (content_fingerprint) WHERE content_fingerprint IS NOT NULL;
COMMIT;
