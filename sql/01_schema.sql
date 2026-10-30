-- @file: sql/01_schema.sql
-- @exports: system_heartbeat, gazetteer_places, raw_reports, incident_clusters, corridor_history, corridor_choke_points
-- @depends_on: PostgreSQL 16, PostGIS 3.4
-- @purpose: Transactional DDL with spatial/trigram indexes; no seed data.
BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE system_heartbeat (
    service_name TEXT PRIMARY KEY,
    last_heartbeat_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_backfill_from_utc TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('initialized','healthy','backfilling','degraded','stopped')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE gazetteer_places (
    geoname_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    ascii_name TEXT NOT NULL,
    alternate_names TEXT[] NOT NULL DEFAULT '{}',
    feature_class CHAR(1),
    feature_code TEXT,
    admin1_state TEXT,
    admin2_district TEXT,
    population BIGINT NOT NULL DEFAULT 0 CHECK (population >= 0),
    geom GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX gazetteer_geom_gist ON gazetteer_places USING GIST (geom);
CREATE INDEX gazetteer_ascii_trgm_gin ON gazetteer_places USING GIN (ascii_name gin_trgm_ops);

CREATE TABLE incident_clusters (
    cluster_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    temporal_status TEXT NOT NULL DEFAULT 'unknown',
    corridor_name TEXT, primary_landmark TEXT, locality TEXT, city TEXT, state TEXT,
    lanes_blocked INTEGER CHECK (lanes_blocked BETWEEN 0 AND 30),
    total_lanes INTEGER CHECK (total_lanes BETWEEN 1 AND 30),
    direction TEXT, summary_reason TEXT,
    report_count INTEGER NOT NULL DEFAULT 1 CHECK (report_count > 0),
    source_report_ids UUID[] NOT NULL DEFAULT '{}',
    attribute_completeness_score DOUBLE PRECISION CHECK (attribute_completeness_score BETWEEN 0 AND 1),
    pcu_queue_load DOUBLE PRECISION CHECK (pcu_queue_load >= 0 AND pcu_queue_load < 'Infinity'::float8),
    discharge_capacity_pcu_hr DOUBLE PRECISION
        CHECK (discharge_capacity_pcu_hr > 0 AND discharge_capacity_pcu_hr < 'Infinity'::float8),
    waterfall_level_used SMALLINT CHECK (waterfall_level_used BETWEEN 1 AND 3),
    estimated_clearance_mins DOUBLE PRECISION CHECK (estimated_clearance_mins >= 0),
    bayesian_bound_mins DOUBLE PRECISION CHECK (bayesian_bound_mins >= 0),
    hard_ceiling_mins DOUBLE PRECISION CHECK (hard_ceiling_mins > 0),
    first_reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    predicted_clear_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT true,
    centroid_geom GEOMETRY(Point, 4326) NOT NULL,
    impact_polygon_geom GEOMETRY(Polygon, 4326),
    CHECK (lanes_blocked IS NULL OR total_lanes IS NULL OR lanes_blocked <= total_lanes),
    CHECK (last_updated_at >= first_reported_at),
    CHECK (estimated_clearance_mins <= bayesian_bound_mins),
    CHECK (estimated_clearance_mins <= hard_ceiling_mins),
    CHECK (incident_type IN ('collision','landslide','waterlogging','protest','roadwork',
        'road_closure','bridge_damage','congestion','other','unknown')),
    CHECK (severity IN ('minor','moderate','severe','catastrophic','unknown')),
    CHECK (temporal_status IN ('active','cleared','planned','historical','unknown'))
);
CREATE INDEX incident_centroid_gist ON incident_clusters USING GIST (centroid_geom);
CREATE INDEX incident_active_updated_idx ON incident_clusters (is_active, last_updated_at DESC);

CREATE TABLE raw_reports (
    report_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_name TEXT NOT NULL,
    source_type TEXT,
    source_url TEXT UNIQUE NOT NULL,
    content_fingerprint TEXT,
    title TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    setfit_is_relevant BOOLEAN,
    setfit_confidence DOUBLE PRECISION CHECK (setfit_confidence BETWEEN 0 AND 1),
    temporal_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (temporal_status IN ('active','cleared','planned','historical','unknown')),
    landmark_tuple JSONB,
    extracted_attributes JSONB,
    geocode_method TEXT CHECK (geocode_method IN ('gazetteer','geoapify','nominatim','provided','unresolved')),
    geocode_confidence DOUBLE PRECISION CHECK (geocode_confidence BETWEEN 0 AND 1),
    geom GEOMETRY(Point, 4326),
    cluster_id UUID REFERENCES incident_clusters(cluster_id) ON DELETE SET NULL,
    pipeline_status TEXT NOT NULL DEFAULT 'raw'
        CHECK (pipeline_status IN ('raw','filtered','extracted','geocoded','clustered','rejected','dead_letter'))
);
CREATE INDEX raw_reports_geom_gist ON raw_reports USING GIST (geom);
CREATE UNIQUE INDEX raw_reports_fingerprint_idx ON raw_reports (content_fingerprint)
    WHERE content_fingerprint IS NOT NULL;
CREATE INDEX raw_reports_published_status_idx ON raw_reports (published_at DESC, pipeline_status);
CREATE INDEX raw_reports_cluster_idx ON raw_reports (cluster_id);

CREATE TABLE corridor_history (
    history_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    corridor_name TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    lanes_blocked INTEGER CHECK (lanes_blocked BETWEEN 0 AND 30),
    observed_clearance_mins DOUBLE PRECISION NOT NULL
        CHECK (observed_clearance_mins >= 0 AND observed_clearance_mins < 'Infinity'::float8),
    pcu_load DOUBLE PRECISION CHECK (pcu_load >= 0 AND pcu_load < 'Infinity'::float8),
    weather_condition TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    geom GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX corridor_history_geom_gist ON corridor_history USING GIST (geom);
CREATE INDEX corridor_history_lookup_idx ON corridor_history (corridor_name, incident_type, severity);

CREATE TABLE corridor_choke_points (
    choke_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    corridor_name TEXT NOT NULL,
    choke_name TEXT NOT NULL,
    barrier_tags TEXT[] NOT NULL DEFAULT '{}',
    lane_reduction_ratio DOUBLE PRECISION CHECK (lane_reduction_ratio BETWEEN 0 AND 1),
    slope_degrees DOUBLE PRECISION CHECK (slope_degrees BETWEEN 0 AND 90),
    historical_hotspot_score DOUBLE PRECISION CHECK (historical_hotspot_score BETWEEN 0 AND 1),
    static_topology_score DOUBLE PRECISION CHECK (static_topology_score BETWEEN 0 AND 1),
    geom GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX choke_points_geom_gist ON corridor_choke_points USING GIST (geom);
COMMIT;
