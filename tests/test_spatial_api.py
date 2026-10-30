# @file: tests/test_spatial_api.py
# @exports: Spatial API telemetry, strict active records, and offline poison tests
# @depends_on: backend/spatial/{routes,pipeline}.py
# @purpose: Verify API behavior and dead-letter framing without database/network services.
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fakeredis.aioredis import FakeRedis

from backend.core.redis_bus import RedisStreamBus, StreamNames
from backend.main import app
from backend.schemas.spatial import GoldenRecordSchema
from backend.spatial.pipeline import process_extracted_stream_batch
from backend.spatial.worker import SpatialBusy


@pytest.mark.asyncio
@pytest.mark.parametrize('busy', [False, True])
async def test_spatial_drain_endpoint(busy):
    service = SimpleNamespace(process=AsyncMock(return_value={'geocoded_count': 3, 'cache_hits': 3,
        'nominatim_hits': 0, 'clusters_created_or_updated': 3, 'avg_geocode_ms': 2.0, 'dead_letter_count': 0}))
    if busy:
        service.process.side_effect = SpatialBusy('Busy')
    with patch.object(app.state, 'spatial', service, create=True):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            result = await client.post('/api/v1/spatial/process')
    assert result.status_code == (409 if busy else 200)
    if not busy:
        assert result.json()['geocoded_count'] == 3


@pytest.mark.asyncio
async def test_active_endpoint_validates_golden_records_and_fixture_filter():
    identity = uuid4()
    golden = GoldenRecordSchema(report_id=identity, cluster_id=uuid4(), report_count=1,
        source_report_ids=[identity], attribute_completeness_score=0.5, source_scope='fixture',
        location={'longitude': 78, 'latitude': 30, 'method': 'provided', 'confidence': 0.9},
        first_reported_at='2026-10-01T00:00:00Z')
    factory = MagicMock(return_value=AsyncMock())
    with patch.object(app.state, 'session_factory', factory, create=True), \
         patch('backend.spatial.routes.list_active', new_callable=AsyncMock, return_value=[golden]) as read:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            result = await client.get('/api/v1/incidents/active?include_fixtures=false')
            assert result.status_code == 200 and result.json()[0]['report_count'] == 1
            assert read.call_args.args[2] is False
            assert (await client.get('/api/v1/incidents/active?limit=0')).status_code == 422


@pytest.mark.asyncio
async def test_offline_spatial_poison_routes_atomically():
    async with FakeRedis(decode_responses=True) as redis:
        bus = RedisStreamBus(redis)
        await redis.xadd(StreamNames.EXTRACTED_ITEMS, {'payload': '{invalid JSON'})
        session = AsyncMock()
        stats = await process_extracted_stream_batch(session, bus)
        assert stats['dead_letter_count'] == 1
        assert (await redis.xpending(StreamNames.EXTRACTED_ITEMS, 'spatial_geocode_group'))['pending'] == 0
        session.execute.assert_not_awaited()
