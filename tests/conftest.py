# @file: tests/conftest.py
# @exports: ingestion_services
# @depends_on: backend/core/{config,database,redis_bus}.py
# @purpose: Opt-in isolated Postgres schema and in-memory Redis for ingestion transaction tests.
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.core.config import get_settings
from backend.core.database import create_database
from backend.core.redis_bus import RedisStreamBus


@pytest_asyncio.fixture
async def ingestion_services():
    if os.environ.get('TRAFFICSENSE_INTEGRATION') != '1':
        pytest.skip('Requires live Postgres; set TRAFFICSENSE_INTEGRATION=1')
    settings = get_settings()
    admin, _ = create_database(settings)
    schema = 'ts_test_' + uuid4().hex
    engine = None
    client = FakeRedis(decode_responses=True)
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for table in ('raw_reports', 'system_heartbeat', 'ingestion_outbox'):
                await connection.execute(text(
                    f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)'
                ))
        engine = create_async_engine(settings.database_url.get_secret_value(), connect_args={
            'server_settings': {'search_path': f'{schema},public'}, 'command_timeout': 5,
        })
        yield async_sessionmaker(engine, expire_on_commit=False), RedisStreamBus(client)
    finally:
        await client.aclose()
        if engine:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
