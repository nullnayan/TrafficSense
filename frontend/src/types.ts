// @file: frontend/src/types.ts
// @exports: GoldenRecord, Waterfall, ScoutResult, QueryResult, Theme, StreamEnvelope
// @depends_on: backend/schemas/{spatial,assistant,route_hazard}.py
// @purpose: Typed API records shared by map, inspectors, route scout and safe query UI.
export type Theme = 'civic_white_cad' | 'highway_paper_blueprint' | 'asphalt_carbon_radar';
export type Location = {longitude: number; latitude: number; display_name?: string; confidence: number; method: string};
export type Geometry = Exclude<import('geojson').Geometry, {type: 'GeometryCollection'}>;
export type Waterfall = {
  waterfall_level_used: number; empirical_minutes: number | null; neighbor_minutes: number | null;
  physical_minutes: number | null; weights: Record<string, number>; n1: number; n2: number;
  estimated_clearance_mins: number; bayesian_bound_mins: number; hard_ceiling_mins: number;
  floor_minutes: number; pcu_queue_load: number | null; discharge_capacity_pcu_hr: number | null;
  lane_input_mode: string | null; physical_assumptions: Record<string, unknown>;
  component_provenance: Record<string, unknown>; provenance: string; uncertainty_method?: string;
};
export type GoldenRecord = {
  cluster_id: string; incident_type: string; severity: string; temporal_status: string;
  landmark_tuple: {primary_landmark?: string; corridor_name?: string; city?: string; state?: string};
  lanes_blocked: number | null; total_lanes: number | null; report_count: number; source_report_ids: string[];
  attribute_completeness_score: number; location: Location; first_reported_at: string; last_updated_at: string;
  expected_clearance_minutes: number | null; clearance_upper_bound_minutes: number | null;
  estimated_clear_at: string | null; predicted_clear_at: string | null; summary_reason: string | null;
  direction: string | null; is_active: boolean; source_scope: 'live' | 'fixture';
  impact_polygon_geojson: Geometry | null; clearance: Waterfall | null; waterfall_breakdown: Waterfall | null;
};
export type Overlap = 'BLOCKED_AT_ETA' | 'RESIDUAL_RISK' | 'CLEARED_BEFORE_ETA';
export type Hazard = {cluster_id: string; overlap: Overlap; arrival_at: string; estimated_clear_at: string;
  clear_upper_at: string; delay_minutes: number; distance_along_route_meters: number; golden_record: GoldenRecord};
export type Choke = {choke_id: string; choke_name: string; location: Location; risk_score: number;
  delay_minutes: number; arrival_minutes: number; predicted_clearance_minutes: number | null;
  telemetry_source: 'tomtom_live' | 'here_live' | 'local_incident_clusters'; live_speed_ratio: number | null;
  current_speed_kmh: number | null; free_flow_speed_kmh: number | null; traffic_cache_hit: boolean};
export type ScoutResult = {route_geojson: Geometry; route_source: string; distance_meters: number;
  duration_seconds: number; adjusted_duration_seconds: number; baseline_eta: string; adjusted_eta: string;
  risk_level: string; recommendation: string; advisory: string; incidents: Hazard[]; choke_points: Choke[];
  modeled_choke_delay_minutes?: number; blocked_wait_minutes?: number; residual_delay_minutes?: number;
  journey_status?: 'open' | 'departure_hold'};
export type QueryResult = {template_id: string; parameters: Record<string, unknown>; explanation: string;
  results: Record<string, unknown>[]; row_count: number; slot_source: string; include_fixtures: boolean};
export type StreamEnvelope = {golden_record: GoldenRecord; superseded_cluster_ids: string[]; snapshot?: boolean};
export type TerrainPoint = {altitude: number | null; slope: number | null};
export type Cursor = {longitude: number; latitude: number; altitude: number | null};
