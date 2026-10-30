# @file: backend/ingestion/pipeline.py
# @exports: ingest_window
# @depends_on: backend/ingestion/{storage,connectors}.py
# @purpose: Isolated source failures, persistent deduplication, delivery recovery, and telemetry.
import asyncio

from sqlalchemy import text

from backend.ingestion.deduplication import deduplicate
from backend.ingestion.storage import flush_outbox, persist_items, write_heartbeat

SOURCE_TIMEOUT_SECONDS = 6


async def ingest_window(session, bus, connectors, since, until, backfill=False):
    summary = dict(fetched_count=0, inserted_count=0, published_count=0, dropped_noise_count=0,
                   delivered_count=0, pending_count=0, source_errors={}, fallback_sources=[],
                   fixture_sources=[], since_utc=since.isoformat(), until_utc=until.isoformat(),
                   backfill=backfill, worker_running=True, active_providers={})
    results = await asyncio.gather(*(asyncio.wait_for(source.fetch(since, until), SOURCE_TIMEOUT_SECONDS)
                                    for source in connectors), return_exceptions=True)
    items = []
    for index, (source, result) in enumerate(zip(connectors, results)):
        name = f'{source.name}:{index}'
        summary['active_providers'][source.name] = {
            'enabled': True, 'returned_count': 0 if isinstance(result, BaseException) else len(result),
            'status': type(result).__name__ if isinstance(result, BaseException) else 'ok', 'mode': source.mode}
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            summary['source_errors'][name] = type(result).__name__
            continue
        if source.last_error or source.invalid_count or source.truncated:
            summary['source_errors'][name] = source.last_error or (
                'truncated_response' if source.truncated else f'invalid_records:{source.invalid_count}'
            )
        if source.used_fixture:
            summary['fixture_sources'].append(name)
            if source.mode != 'fixtures':
                summary['fallback_sources'].append(name)
        items.extend(result)
    if not connectors:
        summary['source_errors']['configuration'] = 'no_active_connectors'
    summary['fetched_count'] = len(items)
    items = deduplicate(items)
    summary['deduplicated_count'] = summary['fetched_count']-len(items)
    summary['inserted_count'], summary['dropped_noise_count'] = await persist_items(session, items)
    try:
        summary['published_count'], summary['delivered_count'] = await flush_outbox(session, bus)
    except Exception as exc:
        await session.rollback()
        summary['source_errors']['redis_delivery'] = type(exc).__name__
    async with session.begin():
        summary['pending_count'] = (await session.execute(text(
            'SELECT count(*) FROM ingestion_outbox WHERE delivered_at IS NULL'
        ))).scalar_one()
    await write_heartbeat(session, since, until, summary, backfill)
    summary['status'] = 'degraded' if summary['source_errors'] or summary['fallback_sources'] or summary['pending_count'] else 'healthy'
    return summary
