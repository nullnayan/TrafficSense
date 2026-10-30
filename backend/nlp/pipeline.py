# @file: backend/nlp/pipeline.py
# @exports: process_raw_stream_batch, process_filtered_stream_batch
# @depends_on: backend/nlp/{storage,gatekeeper_model,extractor}.py
# @purpose: Pending recovery, bounded poison retries, durable stage publication, then source ACK.
import asyncio

import httpx
from asyncpg import PostgresError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from backend.core.config import get_settings
from backend.core.redis_bus import StreamNames
from backend.nlp.extractor import IncidentExtractor
from backend.nlp.gatekeeper_model import get_gatekeeper
from backend.nlp.storage import deliver_stage, prepare_stage
from backend.schemas.ingestion import RawIngestedItem

GROUPS = {'filter': ('nlp_filter_group', StreamNames.RAW_ITEMS),
          'extract': ('nlp_extract_group', StreamNames.FILTERED_ITEMS)}


async def _entries(bus, stream, group, consumer, count):
    await bus.ensure_group(stream, group)
    entries = await bus.read_batch(stream, group, consumer, count=count, pending=True)
    if len(entries) < count:
        _, reclaimed = await bus.reclaim_pending(stream, group, consumer, count=count-len(entries))
        entries.extend(reclaimed)
    if len(entries) < count:
        entries.extend(await bus.read_batch(stream, group, consumer, count=count-len(entries), block_ms=1))
    return list({entry_id: fields for entry_id, fields in entries}.items())


async def _batch(session, bus, stage, classifier=None, extractor=None, consumer='nlp_api', count=None):
    settings = get_settings()
    classifier, extractor = classifier or get_gatekeeper(), extractor or IncidentExtractor()
    group, stream = GROUPS[stage]
    entries = await _entries(bus, stream, group, consumer, count or settings.nlp_batch_size)
    stats = dict(processed_raw=0, active_forwarded=0, dropped_noise_or_inactive=0,
                 extracted_count=0, dead_letter_count=0, deferred_count=0, handled_count=0)
    for message_id, fields in entries:
        for attempt in range(settings.nlp_max_attempts):
            try:
                item = RawIngestedItem.model_validate_json(fields['payload'])
                changes = await prepare_stage(session, item.report_id, stage, classifier, extractor)
                for key, value in changes.items():
                    stats[key] += value
                forwarded = await deliver_stage(session, bus, item.report_id, stage)
                stats['active_forwarded'] += int(forwarded and stage == 'filter')
                await bus.ack(stream, group, message_id)
                stats['handled_count'] += 1
                break
            except (ValueError, KeyError, TypeError) as exc:
                await session.rollback()
                if attempt + 1 < settings.nlp_max_attempts:
                    await asyncio.sleep(0.05 * 2 ** attempt)
                    continue
                routed = await bus.dead_letter(stream, group, message_id, f'{type(exc).__name__}: invalid {stage} payload')
                stats['dead_letter_count'] += int(bool(routed))
                stats['handled_count'] += 1
            except (httpx.HTTPError, RedisError, SQLAlchemyError, PostgresError, OSError):
                await session.rollback()
                stats['deferred_count'] += 1
                break
    return stats


async def process_raw_stream_batch(session, bus, **kwargs):
    return await _batch(session, bus, 'filter', **kwargs)


async def process_filtered_stream_batch(session, bus, **kwargs):
    return await _batch(session, bus, 'extract', **kwargs)
