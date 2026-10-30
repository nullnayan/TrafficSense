# @file: backend/analytics/pipeline.py
# @exports: GROUP, process_geocoded_stream_batch
# @depends_on: backend/analytics/{storage,lifecycle}.py, backend/core/redis_bus.py
# @purpose: Bounded pending recovery, latest-revision delivery, poison isolation and expiry telemetry.
import json

from backend.analytics.lifecycle import expire_elapsed_clusters
from backend.analytics.storage import deliver_analytics, flush_outbox, prepare_analytics
from backend.core.redis_bus import StreamNames
from backend.schemas.ingestion import utc_now
from backend.schemas.spatial import SpatialReportEnvelope

GROUP = 'analytics_waterfall_group'
STREAM = StreamNames.GEOCODED_ITEMS


async def process_geocoded_stream_batch(session, bus, consumer='analytics-native', count=50,
                                       max_attempts=3, include_seed=False, reclaim_cursor='0-0', now=None):
    await bus.ensure_group(STREAM, GROUP)
    result = {'clusters_analyzed': 0, 'level1_count': 0, 'level2_count': 0, 'level3_count': 0,
              'clusters_expired': 0, 'avg_expected_clearance_min': 0.0, 'dead_letter_count': 0,
              'processed_count': 0, 'published_count': 0, 'expected_clearance_sum': 0.0}
    result['published_count'], result['outbox_processed_count'] = await flush_outbox(session, bus, count)
    pending = await bus.read_batch(STREAM, GROUP, consumer, count=count, pending=True)
    cursor, reclaimed = await bus.reclaim_pending(STREAM, GROUP, consumer, count=count,
                                                start_id=reclaim_cursor)
    entries = dict(list(dict(pending + reclaimed).items())[:count])
    if len(entries) < count:
        entries.update(await bus.read_batch(STREAM, GROUP, consumer, count=count-len(entries), block_ms=1))
    result['reclaim_cursor'] = cursor
    for message_id, fields in entries.items():
        attempt_key = f'ts:analytics:attempts:{message_id}'
        try:
            envelope = SpatialReportEnvelope.model_validate(json.loads(fields['payload']))
            key, breakdown = await prepare_analytics(session, envelope, include_seed)
            if key is not None:
                result['published_count'] += int(await deliver_analytics(session, bus, key))
            if breakdown:
                result['clusters_analyzed'] += 1
                result[f'level{breakdown.selected_level}_count'] += 1
                result['expected_clearance_sum'] += breakdown.estimated_clearance_mins
            await bus.ack(STREAM, GROUP, message_id)
            await bus.client.delete(attempt_key)
            result['processed_count'] += 1
        except (ValueError, KeyError, TypeError) as exc:
            await session.rollback()
            attempts = await bus.client.incr(attempt_key)
            if attempts >= max_attempts:
                routed = await bus.dead_letter(STREAM, GROUP, message_id, str(exc))
                result['dead_letter_count'] += int(routed is not None)
                await bus.client.delete(attempt_key)
            result['processed_count'] += 1
    async with session.begin():
        result['clusters_expired'] = await expire_elapsed_clusters(session, now or utc_now())
    if result['clusters_analyzed']:
        result['avg_expected_clearance_min'] = result['expected_clearance_sum'] / result['clusters_analyzed']
    return result
