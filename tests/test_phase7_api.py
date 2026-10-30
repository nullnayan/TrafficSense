# @file: tests/test_phase7_api.py
# @exports: Phase 7 HTTP contract tests
# @depends_on: backend/main.py, backend/streaming
# @purpose: Verify SSE aliases, bounded validation, local CORS and recoverable demo HTTP errors.
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fakeredis.aioredis import FakeRedis

from backend.core.redis_bus import RedisStreamBus
from backend.main import app


@pytest.mark.asyncio
async def test_sse_aliases_bounds_and_local_cors():
    async with FakeRedis(decode_responses=True) as redis:
        with patch.object(app.state, 'bus', RedisStreamBus(redis), create=True), \
             patch.object(app.state, 'settings', SimpleNamespace(sse_heartbeat_seconds=.01), create=True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                for path in ('/api/v1/events/stream', '/api/v1/stream/events'):
                    result = await client.get(path, params={'max_events': 3})
                    assert result.status_code == 200 and result.text.count('event:') == 3
                    assert 'event: heartbeat' in result.text
                    assert result.headers['content-type'].startswith('text/event-stream')
                assert (await client.get('/api/v1/events/stream?max_events=0')).status_code == 422
                allowed = await client.options('/api/v1/pipeline/run-demo', headers={
                    'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST'})
                assert allowed.headers['access-control-allow-origin'] == 'http://localhost:5173'
                denied = await client.options('/api/v1/pipeline/run-demo', headers={
                    'Origin': 'https://untrusted.invalid', 'Access-Control-Request-Method': 'POST'})
                assert 'access-control-allow-origin' not in denied.headers


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_demo_endpoint_telemetry_and_recovery(failure):
    operation = AsyncMock(return_value={'source_scope': 'fixture', 'stages': {'ingest': {}}})
    if failure:
        operation.side_effect = OSError('Database offline')
    with patch('backend.streaming.demo_pipeline.run_demo', operation):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            result = await client.post('/api/v1/pipeline/run-demo')
    assert result.status_code == (503 if failure else 200)
    if not failure:
        assert result.json()['source_scope'] == 'fixture'
