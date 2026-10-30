# @file: backend/ingestion/catchup.py
# @exports: inspect_downtime_gap, run_catchup_backfill
# @depends_on: backend/core/config.py, backend/ingestion/pipeline.py
# @purpose: Bounded persisted downtime inspection and startup historical ingestion.
from datetime import datetime, timedelta

from sqlalchemy import text

from backend.core.config import get_settings
from backend.ingestion.pipeline import ingest_window
from backend.schemas.ingestion import utc_now


async def inspect_downtime_gap(session) -> tuple[datetime, datetime, float]:
    settings, until = get_settings(), utc_now()
    async with session.begin():
        last = (await session.execute(text('''
            SELECT CASE WHEN :live AND jsonb_array_length(details->'fixture_sources') > 0
                THEN NULL ELSE last_heartbeat_utc END FROM system_heartbeat
            WHERE service_name='national_macro_sentry'
        '''), {'live': settings.ingestion_source_mode != 'fixtures'})).scalar_one_or_none()
    earliest = until - timedelta(hours=settings.ingestion_max_lookback_hours)
    since = max(last or earliest, earliest)
    # A future timestamp must not generate a negative or reversed request window.
    since = min(since, until)
    return since, until, (until - since).total_seconds() / 3600


async def run_catchup_backfill(session, bus, connectors):
    since, until, gap_hours = await inspect_downtime_gap(session)
    if gap_hours <= get_settings().ingestion_catchup_threshold_hours:
        return {'backfill': False, 'gap_hours': gap_hours, 'skipped': True}
    summary = await ingest_window(session, bus, connectors, since, until, backfill=True)
    summary['gap_hours'] = gap_hours
    return summary
