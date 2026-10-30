-- @file: sql/07_scout_assistant.sql
-- @exports: Scout geography indexes and assistant scope/time index
-- @depends_on: sql/01_schema.sql, sql/05_spatial_pipeline.sql, sql/06_analytics_pipeline.sql
-- @purpose: Repeatable metric route/proximity and read-only assistant lookup acceleration.
BEGIN;
CREATE INDEX IF NOT EXISTS scout_choke_geography_gist
    ON corridor_choke_points USING GIST ((geom::geography));
CREATE INDEX IF NOT EXISTS scout_centroid_geography_gist
    ON incident_clusters USING GIST ((centroid_geom::geography)) WHERE is_active;
CREATE INDEX IF NOT EXISTS scout_impact_geography_gist
    ON incident_clusters USING GIST ((impact_polygon_geom::geography)) WHERE is_active;
CREATE INDEX IF NOT EXISTS assistant_active_scope_time_idx
    ON incident_clusters (source_scope,last_reported_at DESC) WHERE is_active;
CREATE INDEX IF NOT EXISTS assistant_gazetteer_geography_gist
    ON gazetteer_places USING GIST ((geom::geography));
COMMIT;
