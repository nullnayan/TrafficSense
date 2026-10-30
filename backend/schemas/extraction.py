# @file: backend/schemas/extraction.py
# @exports: SetFitGatekeeperResult, LandmarkTuple, ExtractedIncidentSchema
# @depends_on: backend/schemas/enums.py
# @purpose: Abstaining structured NLP output with lane and temporal validation.
from typing import Annotated, Optional, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from backend.schemas.enums import ContractModel, IncidentType, Severity, TemporalStatus

Confidence = Annotated[float, Field(ge=0, le=1)]
LaneCount = Annotated[int, Field(ge=0, le=30, strict=True)]


class SetFitGatekeeperResult(ContractModel):
    report_id: UUID
    is_relevant: bool
    confidence: Confidence
    temporal_status: TemporalStatus = TemporalStatus.UNKNOWN
    model_version: str = Field(min_length=1)


class LandmarkTuple(ContractModel):
    primary_landmark: str | None = None
    corridor_name: str | None = None
    locality: str | None = None
    city: str | None = None
    state: str | None = None
    country_code: str = Field(default='IN', pattern='^IN$')


class ExtractedIncidentSchema(ContractModel):
    report_id: UUID
    incident_type: IncidentType = IncidentType.UNKNOWN
    severity: Severity = Severity.UNKNOWN
    temporal_status: TemporalStatus = TemporalStatus.UNKNOWN
    landmark_tuple: LandmarkTuple = Field(default_factory=LandmarkTuple)
    lanes_blocked: Optional[LaneCount] = None
    total_lanes: Optional[Annotated[int, Field(ge=1, le=30, strict=True)]] = None
    direction: str | None = Field(default=None, max_length=200)
    summary_reason: str | None = Field(default=None, max_length=2000)
    occurred_at: AwareDatetime | None = None
    extraction_confidence: Confidence | None = None

    @model_validator(mode='after')
    def lane_consistency(self) -> Self:
        if (self.lanes_blocked is not None and self.total_lanes is not None
                and self.lanes_blocked > self.total_lanes):
            raise ValueError('lanes_blocked cannot exceed total_lanes')
        return self
