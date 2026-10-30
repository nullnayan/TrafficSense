# @file: backend/streaming/sse.py
# @exports: router, sse_frames, frame
# @depends_on: backend/core/redis_bus.py, backend/spatial/records.py
# @purpose: Independent XREAD cursors, active replay and bounded SSE fan-out without worker ACKs.
import asyncio
import json
import logging
from time import monotonic

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from redis.exceptions import RedisError

from backend.core.redis_bus import StreamNames
from backend.schemas.ingestion import utc_now
from backend.schemas.spatial import GoldenRecordSchema
from backend.spatial.records import list_active

router = APIRouter(prefix='/api/v1', tags=['streaming'])
logger = logging.getLogger(__name__)
STREAMS = (StreamNames.GEOCODED_ITEMS, StreamNames.ANALYZED_ITEMS)


def frame(event, payload, event_id=None):
    identity = f'id: {event_id}\n' if event_id else ''
    return f'{identity}event: {event}\ndata: {json.dumps(payload, allow_nan=False)}\n\n'


def cursor_id(cursors):
    return '|'.join(cursors[str(stream)] for stream in STREAMS)


async def sse_frames(request, include_fixtures=False, replay_active=False, max_events=None):
    state = request.app.state
    client = getattr(getattr(state, 'bus', None), 'client', None)
    cursors = {str(stream): '0-0' for stream in STREAMS}
    if client is not None:
        # Capture tails BEFORE ready/snapshot: writes during replay are never lost.
        for stream in STREAMS:
            tail = await client.xrevrange(stream, count=1)
            cursors[str(stream)] = tail[0][0] if tail else '0-0'
        resume = getattr(request, 'headers', {}).get('last-event-id', '').split('|')
        if len(resume) == 2 and all(part.replace('-', '').isdigit() and
                                    len(part.split('-')) == 2 for part in resume):
            cursors = dict(zip(map(str, STREAMS), resume, strict=True))
    # Legacy unit callers have no bus. Preserve their exact framing contract.
    yield frame('ready', {'phase': 7, 'mode': 'live', 'replaying': replay_active}
                if client is not None else {'phase': 1, 'mode': 'stub'})
    emitted = 1
    if max_events is not None and emitted >= max_events:
        return
    if client is not None:
        if replay_active:
            async with state.session_factory() as session:
                records = await list_active(session, 1000, include_fixtures)
            for record in records:
                yield frame('incident_active', {'golden_record': record.model_dump(mode='json'),
                            'superseded_cluster_ids': [], 'snapshot': True})
                emitted += 1
                if max_events is not None and emitted >= max_events:
                    return
        yield frame('ready', {'phase': 7, 'mode': 'live', 'replaying': False})
        emitted += 1
    heartbeat = state.settings.sse_heartbeat_seconds
    last_heartbeat = monotonic()
    while (max_events is None or emitted < max_events) and not await request.is_disconnected():
        if client is None:
            await asyncio.sleep(heartbeat)
            entries = []
        else:
            try:
                entries = await client.xread(cursors, count=100,
                                            block=max(1, min(1000, int(heartbeat * 1000))))
            except (RedisError, OSError):
                logger.warning('SSE Redis unavailable; retrying without advancing cursors')
                await asyncio.sleep(min(heartbeat, 1))
                entries = []
        for stream, messages in entries:
            for message_id, fields in messages:
                cursors[str(stream)] = message_id
                try:
                    payload = json.loads(fields['payload'])
                    record = GoldenRecordSchema.model_validate(payload['golden_record'])
                except (KeyError, TypeError, ValueError, ValidationError):
                    logger.warning('Skipping malformed SSE envelope on %s', stream)
                    continue
                if record.source_scope == 'fixture' and not include_fixtures:
                    continue
                payload['golden_record'] = record.model_dump(mode='json')
                payload.setdefault('superseded_cluster_ids', [])
                event = 'incident_analyzed' if stream == STREAMS[1] else 'incident_active'
                yield frame(event, payload, cursor_id(cursors))
                emitted += 1
                if max_events is not None and emitted >= max_events:
                    return
        if client is None or monotonic() - last_heartbeat >= heartbeat:
            yield frame('heartbeat', {'timestamp': utc_now().isoformat()},
                        cursor_id(cursors) if client is not None else None)
            emitted += 1
            last_heartbeat = monotonic()


@router.get('/events/stream')
@router.get('/stream/events', include_in_schema=True)
async def stream_events(request: Request, include_fixtures: bool = False,
                        replay_active: bool = False,
                        max_events: int | None = Query(default=None, ge=1, le=10000)):
    return StreamingResponse(sse_frames(request, include_fixtures, replay_active, max_events),
                             media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
