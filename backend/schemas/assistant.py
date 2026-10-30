# @file: backend/schemas/assistant.py
# @exports: QueryParameters, SlotFillingQuery, ParameterizedSQLResponse, AssistantQueryRequest
# @depends_on: backend/schemas/enums.py
# @purpose: Allowlisted query intents and bound slots; no model-generated executable SQL.
from typing import Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from backend.schemas.enums import ContractModel, IncidentType, QueryIntent, Severity


class QueryParameters(ContractModel):
    corridor_name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_meters: float = Field(default=500, gt=0, le=50000)
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    incident_type: IncidentType | None = None
    severity: Severity | None = None
    limit: int = Field(default=20, ge=1, le=100, strict=True)
    time_window_hours: float = Field(default=24, gt=0, le=8760)

    @model_validator(mode='after')
    def paired_coordinates_and_time(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError('latitude and longitude must be supplied together')
        if self.start_at and self.end_at and self.start_at > self.end_at:
            raise ValueError('start_at cannot follow end_at')
        return self


class SlotFillingQuery(ContractModel):
    intent: QueryIntent
    slots: QueryParameters
    sql_template_id: QueryIntent | None = None

    @model_validator(mode='after')
    def required_slots(self) -> Self:
        if self.sql_template_id is not None and self.sql_template_id != self.intent:
            raise ValueError('Template must match validated intent')
        object.__setattr__(self, 'sql_template_id', self.intent)
        if self.intent == QueryIntent.NEARBY_INCIDENTS and self.slots.latitude is None:
            raise ValueError('Nearby queries require coordinates')
        if self.intent == QueryIntent.CORRIDOR_HISTORY and not self.slots.corridor_name:
            raise ValueError('Corridor history requires corridor_name')
        return self


class ParameterizedSQLResponse(ContractModel):
    # Registry metadata only; executable SQL never appears in this contract.
    template_id: QueryIntent
    parameters: QueryParameters
    explanation: str = Field(min_length=1, max_length=2000)
    results: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=100)
    row_count: int = Field(default=0, ge=0, le=100)
    include_fixtures: bool = False
    slot_source: str = 'deterministic_local'


class AssistantQueryRequest(ContractModel):
    query: str = Field(min_length=1, max_length=2000)
    include_fixtures: bool = False
