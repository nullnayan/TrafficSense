-- @file: sql/05_spatial_pipeline.sql
-- @exports: spatial_stage_outbox, spatial helper columns/indexes, spatial pipeline states
-- @depends_on: sql/01_schema.sql, sql/04_nlp_states.sql
-- @purpose: Repeatable additive geocoding/clustering migration with durable event handoffs.
BEGIN;
ALTER TABLE raw_reports DROP CONSTRAINT IF EXISTS raw_reports_pipeline_status_check;
ALTER TABLE raw_reports ADD CONSTRAINT raw_reports_pipeline_status_check CHECK
    (pipeline_status IN ('raw','filtered','extracted','geocoded','clustered','rejected','dead_letter',
        'FILTERED_ACTIVE','DROPPED_IRRELEVANT','DROPPED_NOT_ACTIVE','EXTRACTED',
        'GEOCODED','CLUSTERED','GEOCODE_FAILED','ANALYZED'));
ALTER TABLE gazetteer_places ADD COLUMN IF NOT EXISTS city_name TEXT;
ALTER TABLE gazetteer_places ADD COLUMN IF NOT EXISTS cache_key TEXT;
ALTER TABLE gazetteer_places ADD COLUMN IF NOT EXISTS search_aliases TEXT;
UPDATE gazetteer_places SET search_aliases=array_to_string(alternate_names,' ')
WHERE search_aliases IS NULL;
CREATE INDEX IF NOT EXISTS gazetteer_aliases_trgm_gin
    ON gazetteer_places USING GIN (search_aliases gin_trgm_ops);
CREATE UNIQUE INDEX IF NOT EXISTS gazetteer_cache_key_idx ON gazetteer_places(cache_key);
CREATE INDEX IF NOT EXISTS gazetteer_city_idx ON gazetteer_places(lower(city_name));
CREATE INDEX IF NOT EXISTS gazetteer_state_idx ON gazetteer_places(lower(admin1_state));
CREATE INDEX IF NOT EXISTS raw_spatial_type_time_idx
    ON raw_reports ((extracted_attributes->>'incident_type'), published_at) WHERE geom IS NOT NULL;
CREATE INDEX IF NOT EXISTS raw_spatial_geography_gist ON raw_reports USING GIST ((geom::geography));
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS last_reported_at TIMESTAMPTZ;
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS canonical_attributes JSONB;
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS cluster_revision BIGINT NOT NULL DEFAULT 1;
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS source_scope TEXT NOT NULL DEFAULT 'live';
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS geocode_confidence DOUBLE PRECISION;
CREATE TABLE IF NOT EXISTS spatial_stage_outbox (
    report_id UUID PRIMARY KEY REFERENCES raw_reports(report_id) ON DELETE CASCADE,
    cluster_id UUID REFERENCES incident_clusters(cluster_id),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS spatial_outbox_pending_idx ON spatial_stage_outbox(created_at)
    WHERE delivered_at IS NULL;
-- Enrich original development fixtures without changing their geometry or provenance.
UPDATE gazetteer_places SET city_name=CASE
    WHEN geoname_id IN (-1,-3,-4,-5) THEN 'Dehradun'
    WHEN geoname_id=-2 THEN 'Rishikesh' WHEN geoname_id=-6 THEN 'Bengaluru'
    WHEN geoname_id=-7 THEN 'Delhi' WHEN geoname_id=-8 THEN 'Noida'
    WHEN geoname_id=-9 THEN 'Gurugram' WHEN geoname_id=-10 THEN 'Patna' END
WHERE geoname_id BETWEEN -10 AND -1 AND city_name IS NULL;
COMMIT;
