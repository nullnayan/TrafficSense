# @file: backend/spatial/pipeline.py
# @exports: process_extracted_stream_batch
# @depends_on: backend/spatial/storage.py, backend/core/redis_bus.py
# @purpose: Recoverable extracted-stream consumer with bounded ungeocodable retries and durable clustering handoff.
import asyncio

import httpx
from asyncpg import PostgresError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from backend.core.config import get_settings
from backend.core.redis_bus import StreamNames
from backend.schemas.extraction import ExtractedIncidentSchema
from backend.spatial.geocoder import Geocoder
from backend.spatial.storage import deliver_spatial, mark_failed, prepare_spatial

GROUP = 'spatial_geocode_group'


async def process_extracted_stream_batch(session, bus, geocoder=None, consumer='spatial_api', count=None):
    settings = get_settings()
    geocoder = geocoder or Geocoder(settings, redis=bus.client)
    count = count or settings.spatial_batch_size
    stream = StreamNames.EXTRACTED_ITEMS
    await bus.ensure_group(stream, GROUP)
    entries = await bus.read_batch(stream, GROUP, consumer, count=count, pending=True)
    if len(entries) < count:
        _, reclaimed = await bus.reclaim_pending(stream, GROUP, consumer, count=count-len(entries))
        entries.extend(reclaimed)
    if len(entries) < count:
        entries.extend(await bus.read_batch(stream, GROUP, consumer, count=count-len(entries), block_ms=1))
    stats = dict(geocoded_count=0, cache_hits=0, geoapify_hits=0, nominatim_hits=0, clusters_created_or_updated=0,
                 geocode_ms=0.0, dead_letter_count=0, deferred_count=0, handled_count=0, published_count=0)
    for message_id, fields in dict(entries).items():
        report_id = None
        for attempt in range(settings.spatial_max_attempts):
            try:
                attributes = ExtractedIncidentSchema.model_validate_json(fields['payload'])
                report_id = attributes.report_id
                changes = await prepare_spatial(session, report_id, geocoder)
                for key, value in changes.items():
                    stats[key] += value
                stats['published_count'] += int(await deliver_spatial(session, bus, report_id))
                await bus.ack(stream, GROUP, message_id)
                stats['handled_count'] += 1
                break
            except (ValueError, KeyError, TypeError) as exc:
                await session.rollback()
                if attempt + 1 < settings.spatial_max_attempts:
                    await asyncio.sleep(0.05 * 2**attempt)
                    continue
                await mark_failed(session, report_id)
                routed = await bus.dead_letter(stream, GROUP, message_id, f'{type(exc).__name__}: spatial resolution failed')
                stats['dead_letter_count'] += int(bool(routed))
                stats['handled_count'] += 1
            except (httpx.HTTPError, RedisError, SQLAlchemyError, PostgresError, OSError):
                await session.rollback()
                stats['deferred_count'] += 1
                break
    return stats
