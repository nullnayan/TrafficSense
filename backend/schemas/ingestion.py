# @file: backend/schemas/ingestion.py
# @exports: RawIngestedItem, HeartbeatStatus, utc_now
# @depends_on: backend/schemas/enums.py
# @purpose: Source provenance and timezone-aware ingestion/heartbeat contracts.
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, HttpUrl, JsonValue

from backend.schemas.enums import ContractModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RawIngestedItem(ContractModel):
    report_id: UUID = Field(default_factory=uuid4)
    source_name: str = Field(min_length=1, max_length=120)
    source_type: str | None = Field(default=None, max_length=120)
    source_url: HttpUrl
    title: str = Field(min_length=1, max_length=1000)
    raw_text: str = Field(min_length=1, max_length=100000)
    published_at: AwareDatetime
    ingested_at: AwareDatetime = Field(default_factory=utc_now)


class HeartbeatStatus(ContractModel):
    service_name: str = Field(min_length=1, max_length=120)
    last_heartbeat_utc: AwareDatetime = Field(default_factory=utc_now)
    last_backfill_from_utc: AwareDatetime | None = None
    status: Literal['initialized', 'healthy', 'backfilling', 'degraded', 'stopped']
    details: dict[str, JsonValue] = Field(default_factory=dict)
