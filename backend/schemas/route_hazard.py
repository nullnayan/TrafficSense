# @file: backend/schemas/route_hazard.py
# @exports: RouteIncident
# @depends_on: backend/schemas/enums.py
# @purpose: Transparent route hazard arrival, clearance deadlines and overlap penalty contract.
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue

from backend.schemas.enums import ContractModel


class RouteIncident(ContractModel):
    cluster_id: UUID
    overlap: Literal['BLOCKED_AT_ETA', 'RESIDUAL_RISK', 'CLEARED_BEFORE_ETA']
    arrival_at: AwareDatetime
    travel_minutes_to_incident: float = Field(ge=0)
    distance_along_route_meters: float = Field(ge=0)
    estimated_clear_at: AwareDatetime
    clear_upper_at: AwareDatetime
    delay_minutes: float = Field(ge=0)
    golden_record: dict[str, JsonValue]
