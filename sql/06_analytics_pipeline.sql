-- @file: sql/06_analytics_pipeline.sql
-- @exports: analytics_stage_outbox, clearance columns/indexes, invalidate_cluster_analytics
-- @depends_on: sql/01_schema.sql, sql/05_spatial_pipeline.sql
-- @purpose: Repeatable scoped clearance analytics, revision invalidation and durable delivery.
BEGIN;
ALTER TABLE raw_reports DROP CONSTRAINT IF EXISTS raw_reports_pipeline_status_check;
ALTER TABLE raw_reports ADD CONSTRAINT raw_reports_pipeline_status_check CHECK
    (pipeline_status IN ('raw','filtered','extracted','geocoded','clustered','rejected','dead_letter',
        'FILTERED_ACTIVE','DROPPED_IRRELEVANT','DROPPED_NOT_ACTIVE','EXTRACTED',
        'GEOCODED','CLUSTERED','GEOCODE_FAILED','ANALYZED'));
ALTER TABLE incident_clusters DROP CONSTRAINT IF EXISTS incident_clusters_incident_type_check;
ALTER TABLE incident_clusters ADD CONSTRAINT incident_clusters_incident_type_check CHECK
    (incident_type IN ('collision','accident','breakdown','construction','landslide','waterlogging',
        'protest','roadwork','road_closure','bridge_damage','congestion','other','unknown'));
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS analyzed_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS expected_clearance_minutes DOUBLE PRECISION;
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS clearance_upper_bound_minutes DOUBLE PRECISION;
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS waterfall_breakdown JSONB;
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS estimated_clear_at TIMESTAMPTZ;
ALTER TABLE incident_clusters ADD COLUMN IF NOT EXISTS expired_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS cluster_clearance_expiry_idx ON incident_clusters(estimated_clear_at)
    WHERE is_active AND estimated_clear_at IS NOT NULL;
ALTER TABLE corridor_history ADD COLUMN IF NOT EXISTS source_scope TEXT NOT NULL DEFAULT 'live';
ALTER TABLE corridor_choke_points ADD COLUMN IF NOT EXISTS source_scope TEXT NOT NULL DEFAULT 'live';
ALTER TABLE corridor_choke_points ADD COLUMN IF NOT EXISTS base_delay_minutes DOUBLE PRECISION
    NOT NULL DEFAULT 10 CHECK (base_delay_minutes >= 0 AND base_delay_minutes < 'Infinity'::float8);
ALTER TABLE corridor_choke_points ADD COLUMN IF NOT EXISTS peak_multiplier DOUBLE PRECISION
    NOT NULL DEFAULT 1.5 CHECK (peak_multiplier >= 1 AND peak_multiplier < 'Infinity'::float8);
ALTER TABLE corridor_choke_points ADD COLUMN IF NOT EXISTS choke_radius_meters DOUBLE PRECISION
    NOT NULL DEFAULT 500 CHECK (choke_radius_meters > 0);
-- Only the reserved original seed identities are relabeled, including on repeat upgrades.
UPDATE corridor_history SET source_scope='fixture'
    WHERE history_id IN (SELECT ('10000000-0000-4000-8000-' || lpad(n::text,12,'0'))::uuid
                        FROM generate_series(1,6) n);
UPDATE corridor_choke_points SET source_scope='fixture'
    WHERE choke_id IN (SELECT ('20000000-0000-4000-8000-' || lpad(n::text,12,'0'))::uuid
                      FROM generate_series(1,5) n);
CREATE INDEX IF NOT EXISTS history_metric_gist ON corridor_history USING GIST ((geom::geography));
CREATE INDEX IF NOT EXISTS choke_metric_gist ON corridor_choke_points USING GIST ((geom::geography));
CREATE INDEX IF NOT EXISTS history_analytics_type_scope_idx ON corridor_history(incident_type,source_scope);
CREATE INDEX IF NOT EXISTS history_normalized_corridor_idx ON corridor_history
    ((regexp_replace(lower(corridor_name),'[^a-z0-9]','','g')),incident_type,source_scope);
CREATE TABLE IF NOT EXISTS analytics_stage_outbox (
    cluster_id UUID NOT NULL REFERENCES incident_clusters(cluster_id) ON DELETE CASCADE,
    cluster_revision BIGINT NOT NULL CHECK (cluster_revision > 0),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    skipped_at TIMESTAMPTZ,
    PRIMARY KEY(cluster_id,cluster_revision)
);
CREATE INDEX IF NOT EXISTS analytics_outbox_pending_idx ON analytics_stage_outbox(created_at)
    WHERE delivered_at IS NULL AND skipped_at IS NULL;
CREATE OR REPLACE FUNCTION invalidate_cluster_analytics() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.cluster_revision <> OLD.cluster_revision THEN
        NEW.expected_clearance_minutes := NULL;
        NEW.clearance_upper_bound_minutes := NULL;
        NEW.waterfall_breakdown := NULL;
        NEW.estimated_clear_at := NULL;
        NEW.predicted_clear_at := NULL;
        NEW.expired_at := NULL;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cluster_analytics_revision ON incident_clusters;
CREATE TRIGGER cluster_analytics_revision BEFORE UPDATE ON incident_clusters
    FOR EACH ROW EXECUTE FUNCTION invalidate_cluster_analytics();
COMMIT;
