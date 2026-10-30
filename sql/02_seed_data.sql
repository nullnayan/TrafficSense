-- @file: sql/02_seed_data.sql
-- @exports: 1 heartbeat, 10 benchmark landmarks, 6 synthetic history rows, 5 synthetic choke points
-- @depends_on: sql/01_schema.sql
-- @purpose: Repeatable development fixtures; coordinates approximate and clearance values unmeasured.
BEGIN;
INSERT INTO system_heartbeat (service_name, status, details)
VALUES ('national_macro_sentry', 'initialized',
    '{"phase":1,"worker_running":false,"seed_kind":"development_fixture"}')
ON CONFLICT (service_name) DO NOTHING;

-- Negative IDs reserve a fixture namespace; these are NOT real GeoNames identifiers.
INSERT INTO gazetteer_places
    (geoname_id, name, ascii_name, alternate_names, feature_class, feature_code,
     admin1_state, admin2_district, geom)
SELECT id, name, name, aliases, 'S', 'LMRK', state, district,
    ST_SetSRID(ST_MakePoint(lon, lat), 4326)
FROM (VALUES
    (-1, 'Dehradun Clock Tower', ARRAY['Ghanta Ghar'], 'Uttarakhand', 'Dehradun', 78.0418, 30.3256),
    (-2, 'Laxman Jhula NH-7', ARRAY['Lakshman Jhula','Rishikesh access'], 'Uttarakhand', 'Tehri Garhwal', 78.3296, 30.1264),
    (-3, 'ISBT Dehradun', ARRAY['Inter State Bus Terminal'], 'Uttarakhand', 'Dehradun', 78.0095, 30.2883),
    (-4, 'Mussoorie Diversion', ARRAY['Mussoorie Road Diversion'], 'Uttarakhand', 'Dehradun', 78.0680, 30.3865),
    (-5, 'Kali Mandir Choke Point', ARRAY['NH-7 Kali Mandir fixture'], 'Uttarakhand', 'Dehradun', 78.2070, 30.1720),
    (-6, 'Silk Board Bengaluru', ARRAY['Central Silk Board'], 'Karnataka', 'Bengaluru Urban', 77.6227, 12.9176),
    (-7, 'Kashmere Gate Delhi', ARRAY['Kashmiri Gate'], 'Delhi', 'Central Delhi', 77.2284, 28.6675),
    (-8, 'DND Flyway Noida', ARRAY['Delhi Noida Direct Flyway'], 'Uttar Pradesh', 'Gautam Buddha Nagar', 77.3090, 28.5680),
    (-9, 'Rajiv Chowk Gurugram', ARRAY['Rajiv Chowk Gurgaon'], 'Haryana', 'Gurugram', 77.0330, 28.4470),
    (-10, 'Gandhi Setu Patna', ARRAY['Mahatma Gandhi Setu'], 'Bihar', 'Patna', 85.2060, 25.6220)
) AS fixtures(id, name, aliases, state, district, lon, lat)
ON CONFLICT (geoname_id) DO NOTHING;

-- Synthetic values exercise the empirical-history fallback interface only.
-- Replace with sourced, observed measurements before training or evaluating predictions.
INSERT INTO corridor_history
    (history_id, corridor_name, incident_type, severity, lanes_blocked,
     observed_clearance_mins, pcu_load, weather_condition, recorded_at, geom)
SELECT ('10000000-0000-4000-8000-' || lpad(id::text, 12, '0'))::uuid,
    'Dehradun-Rishikesh NH-7', kind, severity, lanes, minutes, pcu,
    'SYNTHETIC_FIXTURE:' || weather, '2026-01-01T06:00:00Z'::timestamptz + id * interval '1 day',
    ST_SetSRID(ST_MakePoint(lon, lat), 4326)
FROM (VALUES
    (1, 'collision', 'moderate', 1, 45, 180, 'dry', 78.0920, 30.2350),
    (2, 'collision', 'severe', 2, 110, 420, 'rain', 78.1400, 30.1800),
    (3, 'landslide', 'severe', 2, 240, 600, 'rain', 78.2070, 30.1720),
    (4, 'waterlogging', 'moderate', 1, 75, 230, 'heavy_rain', 78.2480, 30.1470),
    (5, 'roadwork', 'moderate', 1, 150, 300, 'dry', 78.2810, 30.1250),
    (6, 'protest', 'severe', 2, 120, 380, 'dry', 78.2920, 30.1100)
) AS fixtures(id, kind, severity, lanes, minutes, pcu, weather, lon, lat)
ON CONFLICT (history_id) DO NOTHING;

-- Approximate corridor demonstration points; scores, slopes and barriers are synthetic.
INSERT INTO corridor_choke_points
    (choke_id, corridor_name, choke_name, barrier_tags, lane_reduction_ratio,
     slope_degrees, historical_hotspot_score, static_topology_score, geom)
SELECT ('20000000-0000-4000-8000-' || lpad(id::text, 12, '0'))::uuid,
    'Dehradun-Rishikesh NH-7', name, tags || ARRAY['synthetic_fixture'], ratio, slope, hotspot, score,
    ST_SetSRID(ST_MakePoint(lon, lat), 4326)
FROM (VALUES
    (1, 'Doiwala Approach', ARRAY['junction','lane_merge'], 0.35, 2, 0.60, 0.55, 78.1400, 30.1800),
    (2, 'Kali Mandir Choke Point', ARRAY['curve','hillside'], 0.50, 12, 0.85, 0.88, 78.2070, 30.1720),
    (3, 'Ranipokhri Bridge Approach', ARRAY['bridge','narrow_approach'], 0.45, 4, 0.75, 0.78, 78.2480, 30.1470),
    (4, 'Rishikesh Bypass Merge', ARRAY['junction','lane_merge'], 0.40, 3, 0.70, 0.67, 78.2810, 30.1250),
    (5, 'Rishikesh Access Bottleneck', ARRAY['urban_entry','pedestrian_crossing'], 0.30, 2, 0.65, 0.62, 78.2920, 30.1100)
) AS fixtures(id, name, tags, ratio, slope, hotspot, score, lon, lat)
ON CONFLICT (choke_id) DO NOTHING;
COMMIT;
