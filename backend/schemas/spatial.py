# @file: backend/schemas/spatial.py
# @exports: GeocodedLocation, WaterfallBreakdown, GoldenRecordSchema, RouteScoutRequest, RouteScoutResponse
# @depends_on: backend/schemas/extraction.py, backend/schemas/ingestion.py
# @purpose: Spatial, bounded clearance, canonical event, and route-advisory contracts.
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from backend.schemas.enums import ContractModel, GeocodeMethod
from backend.schemas.extraction import Confidence, ExtractedIncidentSchema
from backend.schemas.ingestion import utc_now
from backend.schemas.route_hazard import RouteIncident

Nonnegative = Annotated[float, Field(ge=0)]


class GeocodedLocation(ContractModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    method: GeocodeMethod
    confidence: Confidence
    geoname_id: int | None = None
    display_name: str | None = None
    query_latency_ms: float = Field(default=0, ge=0)

    @model_validator(mode='after')
    def resolved_coordinates(self) -> Self:
        if self.method == GeocodeMethod.UNRESOLVED:
            raise ValueError('Use null location for unresolved geocoding; never fabricate coordinates')
        return self


class WaterfallBreakdown(ContractModel):
    waterfall_level_used: Literal[1, 2, 3]
    pcu_queue_load: Nonnegative | None = None
    discharge_capacity_pcu_hr: Annotated[float, Field(gt=0)] | None = None
    estimated_clearance_mins: Nonnegative
    bayesian_bound_mins: Nonnegative
    hard_ceiling_mins: Annotated[float, Field(gt=0)]
    provenance: str = Field(min_length=1, max_length=1000)
    selected_level: Literal[1, 2, 3] | None = None
    empirical_minutes: Nonnegative | None = None
    neighbor_minutes: Nonnegative | None = None
    physical_minutes: Nonnegative | None = None
    n1: int = Field(default=0, ge=0)
    n2: int = Field(default=0, ge=0)
    empirical_variance: Nonnegative = 0
    neighbor_variance: Nonnegative = 0
    weights: dict[str, float] = Field(default_factory=dict)
    upper_bound_minutes: Nonnegative | None = None
    floor_minutes: Nonnegative = 0
    lane_input_mode: Literal['explicit_ratio', 'explicit_lanes_default_corridor',
                             'abstained_severity_prior'] | None = None
    effective_capacity_drop_ratio: Annotated[float, Field(ge=0.10, le=0.95)] | None = None
    physical_assumptions: dict[str, JsonValue] = Field(default_factory=dict)
    component_provenance: dict[str, JsonValue] = Field(default_factory=dict)
    uncertainty_method: str | None = None

    @model_validator(mode='after')
    def bounded_estimate(self) -> Self:
        if self.estimated_clearance_mins > min(self.bayesian_bound_mins, self.hard_ceiling_mins):
            raise ValueError('Estimated clearance must respect Bayesian and hard upper bounds')
        if self.bayesian_bound_mins > self.hard_ceiling_mins:
            raise ValueError('Bayesian upper bound must respect hard ceiling')
        if self.estimated_clearance_mins < self.floor_minutes:
            raise ValueError('Estimate must respect type floor')
        return self


class GoldenRecordSchema(ExtractedIncidentSchema):
    cluster_id: UUID
    report_count: int = Field(ge=1)
    source_report_ids: list[UUID] = Field(min_length=1)
    attribute_completeness_score: Confidence
    location: GeocodedLocation
    clearance: WaterfallBreakdown | None = None
    expected_clearance_minutes: Nonnegative | None = None
    clearance_upper_bound_minutes: Nonnegative | None = None
    waterfall_breakdown: WaterfallBreakdown | None = None
    last_reported_at: AwareDatetime | None = None
    estimated_clear_at: AwareDatetime | None = None
    first_reported_at: AwareDatetime
    last_updated_at: AwareDatetime = Field(default_factory=utc_now)
    predicted_clear_at: AwareDatetime | None = None
    is_active: bool = True
    source_scope: Literal['live', 'fixture'] = 'live'
    impact_polygon_geojson: dict[str, JsonValue] | None = None

    @model_validator(mode='after')
    def canonical_consistency(self) -> Self:
        if len(set(self.source_report_ids)) != self.report_count:
            raise ValueError('report_count must match unique source_report_ids')
        if len(self.source_report_ids) != self.report_count:
            raise ValueError('source_report_ids cannot contain duplicates')
        if self.report_id not in self.source_report_ids:
            raise ValueError('Representative report must be a source report')
        if self.last_updated_at < self.first_reported_at:
            raise ValueError('last_updated_at cannot precede first_reported_at')
        return self


class SpatialReportEnvelope(ContractModel):
    report_id: UUID
    cluster_id: UUID
    cluster_revision: int = Field(ge=1)
    geocoded_location: GeocodedLocation
    golden_record: GoldenRecordSchema
    superseded_cluster_ids: list[UUID] = Field(default_factory=list)


class RouteScoutRequest(ContractModel):
    origin: GeocodedLocation | tuple[Annotated[float, Field(ge=-180, le=180)],
                                     Annotated[float, Field(ge=-90, le=90)]] | Annotated[str, Field(min_length=1, max_length=200)]
    destination: GeocodedLocation | tuple[Annotated[float, Field(ge=-180, le=180)],
                                          Annotated[float, Field(ge=-90, le=90)]] | Annotated[str, Field(min_length=1, max_length=200)]
    departure_at: AwareDatetime = Field(default_factory=utc_now)
    corridor_buffer_meters: float = Field(default=500, gt=0, le=5000)
    max_choke_points: int = Field(default=5, ge=3, le=5)
    include_fixtures: bool = False
    corridor_name: str | None = Field(default=None, max_length=200)


class RouteChokePoint(ContractModel):
    choke_id: UUID
    choke_name: str
    location: GeocodedLocation
    static_topology_score: Confidence
    arrival_minutes: Nonnegative
    predicted_clearance_minutes: Nonnegative | None = None
    overlaps_incident: bool | None = None
    risk_score: Nonnegative = 0
    delay_minutes: Nonnegative = 0
    telemetry_source: Literal['tomtom_live', 'here_live', 'local_incident_clusters'] = 'local_incident_clusters'
    live_speed_ratio: Annotated[float, Field(ge=0, le=1)] | None = None
    current_speed_kmh: Nonnegative | None = None
    free_flow_speed_kmh: Annotated[float, Field(gt=0)] | None = None
    traffic_measured_at: AwareDatetime | None = None
    traffic_cache_hit: bool = False
    traffic_confidence: Confidence | None = None
    weather_context: dict[str, JsonValue] | None = None


class RouteScoutResponse(ContractModel):
    distance_meters: Nonnegative
    duration_seconds: Nonnegative
    choke_points: list[RouteChokePoint] = Field(default_factory=list, max_length=5)
    incident_cluster_ids: list[UUID] = Field(default_factory=list)
    advisory: str
    generated_at: AwareDatetime = Field(default_factory=utc_now)
    route_geojson: dict[str, JsonValue] | None = None
    route_source: Literal['osrm_live', 'corridor_waypoint_fallback'] = 'corridor_waypoint_fallback'
    baseline_eta: AwareDatetime | None = None
    adjusted_eta: AwareDatetime | None = None
    adjusted_duration_seconds: Nonnegative = 0
    incidents: list[RouteIncident] = Field(default_factory=list)
    risk_level: Literal['LOW', 'CAUTION', 'HIGH'] = 'LOW'
    recommendation: str = ''
    topology_cache_hit: bool = False
    modeled_choke_delay_minutes: Nonnegative = 0
    blocked_wait_minutes: Nonnegative = 0
    residual_delay_minutes: Nonnegative = 0
    journey_status: Literal['open', 'departure_hold'] = 'open'
    detour_route_geojson: dict[str, JsonValue] | None = None
    detour_duration_seconds: Nonnegative | None = None
    detour_source: Literal['ors_live'] | None = None
    detour_status: str = 'not_requested'
