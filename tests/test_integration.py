# @file: tests/test_integration.py
# @exports: Opt-in live infrastructure smoke tests
# @depends_on: backend/main.py, backend/core/redis_bus.py, sql/01_schema.sql
# @purpose: Confirm real PostGIS health, indexes/seeds, and Redis Lua/Streams behavior.
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from backend.core.config import get_settings
from backend.core.database import create_database
from backend.core.redis_bus import RedisStreamBus, StreamNames
from backend.main import app, lifespan

pytestmark = pytest.mark.skipif(
    os.environ.get('TRAFFICSENSE_INTEGRATION') != '1', reason='Opt in with TRAFFICSENSE_INTEGRATION=1',
)


@pytest.mark.asyncio
async def test_live_api_readiness():
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/health')
            assert response.status_code == 200, response.text
            data = response.json()
            assert '3.4' in data['database']['postgis_version']
            assert data['database']['gazetteer_count'] >= 10
            assert data['database']['heartbeat_timestamp']
            assert data['redis']['status'] == 'ok'


@pytest.mark.asyncio
async def test_live_spatial_fixtures_and_indexes():
    engine, _ = create_database(get_settings())
    try:
        async with engine.connect() as connection:
            counts = (await connection.execute(text('''
                SELECT (SELECT count(*) FROM corridor_history) AS history,
                       (SELECT count(*) FROM corridor_choke_points) AS chokes,
                       (SELECT count(*) FROM gazetteer_places WHERE ST_SRID(geom)=4326) AS places
            '''))).one()
            assert counts.history >= 6 and counts.chokes >= 5 and counts.places >= 10
            indexes = (await connection.execute(text('''
                SELECT indexdef FROM pg_indexes WHERE schemaname='public'
            '''))).scalars().all()
            assert any('gin' in index and 'gin_trgm_ops' in index for index in indexes)
            assert sum('USING gist' in index for index in indexes) >= 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_redis_delivery_and_atomic_dead_letter():
    bus = RedisStreamBus.from_url(get_settings().redis_url.get_secret_value())
    # Dedicated random stream; remove only entries and keys created by this test.
    stream = f'ts:test:{uuid4()}'
    dead_id = None
    try:
        await bus.ensure_group(stream, 'smoke')
        source_id = await bus.publish(stream, {'fixture': True})
        batch = await bus.read_batch(stream, 'smoke', 'worker', block_ms=1)
        assert batch[0][0] == source_id
        dead_id = await bus.dead_letter(stream, 'smoke', source_id, 'smoke-test')
        assert dead_id
        assert await bus.dead_letter(stream, 'smoke', source_id, 'repeat') is None
        assert (await bus.client.xpending(stream, 'smoke'))['pending'] == 0
    finally:
        await bus.client.delete(stream)
        if dead_id:
            await bus.client.xdel(StreamNames.DEAD_LETTER, dead_id)
        await bus.close()
