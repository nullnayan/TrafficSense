# @file: tests/test_api.py
# @exports: API lifecycle, health, cleanup, and SSE regression tests
# @depends_on: backend/main.py
# @purpose: Verify healthy/degraded HTTP responses and SSE framing without live infrastructure.
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from asyncpg import InvalidPasswordError
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from backend.main import app, lifespan, sse_frames


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'connection', 'authentication'])
async def test_lifespan_health_and_cleanup(failure):
    healthy = failure is None
    engine, bus = AsyncMock(), AsyncMock()
    bus.ping.return_value = healthy
    details = dict(postgis_version='POSTGIS=3.4', gazetteer_count=10,
                   heartbeat_timestamp='2026-01-01T00:00:00Z')
    probe = AsyncMock(return_value=details)
    if not healthy:
        probe.side_effect = (InvalidPasswordError('offline') if failure == 'authentication'
                             else OperationalError('SELECT', {}, Exception('offline')))
        bus.ping.side_effect = RedisConnectionError('offline')
    with patch('backend.main.create_database', return_value=(engine, object())), \
         patch('backend.main.RedisStreamBus.from_url', return_value=bus), \
         patch('backend.main.verify_postgis_connection', probe):
        async with lifespan(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/health')
                assert response.status_code == (200 if healthy else 503)
                assert response.json()['status'] == ('ok' if healthy else 'degraded')
                assert app.state.startup_health['status'] == response.json()['status']
                if healthy:
                    assert response.json()['database']['gazetteer_count'] == 10
                assert '/api/v1/stream/events' in (await client.get('/openapi.json')).json()['paths']
        engine.dispose.assert_awaited_once()
        bus.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_sse_frames_and_disconnect():
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(sse_heartbeat_seconds=0.001))),
        is_disconnected=AsyncMock(side_effect=[False, True]),
    )
    frames = [frame async for frame in sse_frames(request)]
    assert frames[0].startswith('event: ready\ndata: ')
    assert json.loads(frames[0].split('data: ')[1]) == {'phase': 1, 'mode': 'stub'}
    assert frames[1].startswith('event: heartbeat\ndata: ')
    assert frames[1].endswith('\n\n')
