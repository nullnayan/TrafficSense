# @file: backend/schemas/__init__.py
# @exports: All public ingestion, extraction, spatial, and assistant contracts
# @depends_on: backend/schemas/{enums,ingestion,extraction,spatial,assistant}.py
# @purpose: Stable public imports for workers and API handlers.
from backend.schemas.assistant import ParameterizedSQLResponse, QueryParameters, SlotFillingQuery
from backend.schemas.enums import GeocodeMethod, IncidentType, QueryIntent, Severity, TemporalStatus
from backend.schemas.extraction import ExtractedIncidentSchema, LandmarkTuple, SetFitGatekeeperResult
from backend.schemas.ingestion import HeartbeatStatus, RawIngestedItem
from backend.schemas.spatial import (
    GeocodedLocation,
    GoldenRecordSchema,
    RouteChokePoint,
    RouteScoutRequest,
    RouteScoutResponse,
    WaterfallBreakdown,
)

__all__ = [
    'ParameterizedSQLResponse', 'QueryParameters', 'SlotFillingQuery', 'GeocodeMethod',
    'IncidentType', 'QueryIntent', 'Severity', 'TemporalStatus', 'ExtractedIncidentSchema',
    'LandmarkTuple', 'SetFitGatekeeperResult', 'HeartbeatStatus', 'RawIngestedItem',
    'GeocodedLocation', 'GoldenRecordSchema', 'RouteChokePoint', 'RouteScoutRequest',
    'RouteScoutResponse', 'WaterfallBreakdown',
]
