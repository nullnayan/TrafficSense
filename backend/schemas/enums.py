# @file: backend/schemas/enums.py
# @exports: ContractModel, IncidentType, Severity, TemporalStatus, QueryIntent, GeocodeMethod
# @depends_on: none
# @purpose: Shared closed enums and strict contract base for all API/worker payloads.
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True, allow_inf_nan=False)


class IncidentType(StrEnum):
    ACCIDENT = 'accident'
    BREAKDOWN = 'breakdown'
    CONSTRUCTION = 'construction'
    COLLISION = 'collision'
    LANDSLIDE = 'landslide'
    WATERLOGGING = 'waterlogging'
    PROTEST = 'protest'
    ROADWORK = 'roadwork'
    ROAD_CLOSURE = 'road_closure'
    BRIDGE_DAMAGE = 'bridge_damage'
    CONGESTION = 'congestion'
    OTHER = 'other'
    UNKNOWN = 'unknown'


class Severity(StrEnum):
    MINOR = 'minor'
    MODERATE = 'moderate'
    SEVERE = 'severe'
    CATASTROPHIC = 'catastrophic'
    UNKNOWN = 'unknown'


class TemporalStatus(StrEnum):
    ACTIVE = 'active'
    CLEARED = 'cleared'
    PLANNED = 'planned'
    HISTORICAL = 'historical'
    UNKNOWN = 'unknown'
    SCHEDULED_FUTURE = 'scheduled_future'
    UNRELATED_NOISE = 'unrelated_noise'


class QueryIntent(StrEnum):
    NEARBY_INCIDENTS = 'nearby_incidents'
    CORRIDOR_HISTORY = 'corridor_history'
    AVERAGE_CLEARANCE = 'average_clearance'
    CONGESTION_HOTSPOTS = 'congestion_hotspots'


class GeocodeMethod(StrEnum):
    GAZETTEER = 'gazetteer'
    NOMINATIM = 'nominatim'
    GEOAPIFY = 'geoapify'
    PROVIDED = 'provided'
    UNRESOLVED = 'unresolved'
