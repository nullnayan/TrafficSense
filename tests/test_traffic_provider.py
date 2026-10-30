# @file: tests/test_traffic_provider.py
# @exports: Live provider gating, malformed responses, deadline and cache isolation tests
# @depends_on: backend/scout/traffic_provider.py, tests/test_scout.py
# @purpose: Prove offline/fixture isolation and bounded resilient optional traffic enrichment.
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fakeredis.aioredis import FakeRedis
from redis.exceptions import ConnectionError

from backend.core.config import Settings
from backend.scout.topology import rank_chokes, sample_chokes
from backend.scout.traffic_provider import sample_live_traffic
from tests.test_scout import route


def chokes(count=5):
    rows = [{'choke_id': str(uuid4()), 'choke_name': str(i), 'longitude': 77.1+i*.01,
             'latitude': 30, 'fraction': i/10, 'base_delay_minutes': i+1, 'peak_multiplier': 1}
            for i in range(count)]
    return sample_chokes(rank_chokes(rows, []), [], route())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['disabled', 'fixtures', 'request_fixtures', 'blank_keys'])
async def test_no_http_requests_without_live_opt_in(mode):
    settings = Settings(scout_live_traffic_enabled=mode != 'disabled',
        ingestion_source_mode='fixtures' if mode == 'fixtures' else 'live',
        tomtom_api_key='' if mode == 'blank_keys' else 'test', here_api_key='')
    client = AsyncMock()
    result = await sample_live_traffic(chokes(), None, settings, mode == 'request_fixtures', client)
    client.get.assert_not_awaited()
    assert all(c.telemetry_source == 'local_incident_clusters' for c in result)


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{}, {'flowSegmentData': {'currentSpeed': 5, 'freeFlowSpeed': 0}},
    {'flowSegmentData': {'currentSpeed': -1, 'freeFlowSpeed': 20}},
    {'flowSegmentData': {'currentSpeed': 'NaN', 'freeFlowSpeed': 20}}])
async def test_malformed_and_invalid_speeds_fall_back(payload):
    settings = Settings(scout_live_traffic_enabled=True, ingestion_source_mode='live', tomtom_api_key='test')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        assert all(c.telemetry_source == 'local_incident_clusters' for c in
                   await sample_live_traffic(chokes(), None, settings, client=client))


@pytest.mark.asyncio
async def test_cache_outage_deadline_expiry_and_hard_cap(monkeypatch):
    # Cache assertions tolerate a loaded host; the strict deadline is exercised below.
    monkeypatch.setattr('backend.scout.traffic_provider.TIMEOUT_SECONDS', 30)
    settings = Settings(scout_live_traffic_enabled=True, ingestion_source_mode='live', tomtom_api_key='test')
    calls = []
    async def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'flowSegmentData': {'currentSpeed': 0, 'freeFlowSpeed': 40}})
    rows = chokes() * 2
    failed = AsyncMock()
    failed.get.side_effect = failed.set.side_effect = ConnectionError('offline')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await sample_live_traffic(rows, failed, settings, client=client)
        assert len(result) == len(calls) == 5 and all(c.live_speed_ratio == 0 for c in result)
        async with FakeRedis(decode_responses=True) as redis:
            await sample_live_traffic(rows, redis, settings, client=client)
            keys = await redis.keys('ts:scout:flow:*')
            assert len(keys) == 5
            for key in keys:
                await redis.expire(key, 0)
            # Wait for observable expiry rather than assuming a 10ms clock tick on every host.
            deadline = asyncio.get_running_loop().time() + 2
            while await redis.exists(*keys):
                assert asyncio.get_running_loop().time() < deadline, 'Flow cache entries did not expire'
                await asyncio.sleep(.01)
            await sample_live_traffic(rows, redis, settings, client=client)
            assert len(calls) == 15
            replacement = settings.model_copy(update={'tomtom_api_key': Settings(tomtom_api_key='other').tomtom_api_key})
            await sample_live_traffic(rows, redis, replacement, client=client)
            assert len(calls) == 20
    async def stalled(request):
        await asyncio.sleep(1)
    monkeypatch.setattr('backend.scout.traffic_provider.TIMEOUT_SECONDS', .01)
    async with httpx.AsyncClient(transport=httpx.MockTransport(stalled)) as client:
        result = await asyncio.wait_for(sample_live_traffic(rows, None, settings, client=client), .5)
        assert all(c.telemetry_source == 'local_incident_clusters' for c in result)
