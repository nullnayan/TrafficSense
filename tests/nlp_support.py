# @file: tests/nlp_support.py
# @exports: nlp_services, insert_report
# @depends_on: backend/core/config.py, sql/04_nlp_states.sql
# @purpose: Isolated live Postgres state-transition tests with fresh Redis streams.
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.core.config import get_settings
from backend.core.redis_bus import RedisStreamBus


@pytest_asyncio.fixture
async def nlp_services():
    if os.environ.get('TRAFFICSENSE_INTEGRATION') != '1':
        pytest.skip('Opt in with TRAFFICSENSE_INTEGRATION=1')
    url = get_settings().database_url.get_secret_value()
    admin = create_async_engine(url)
    schema = 'ts_nlp_test_' + uuid4().hex
    engine, client = None, FakeRedis(decode_responses=True)
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for table in ('raw_reports', 'nlp_stage_outbox'):
                await connection.execute(text(f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)'))
        engine = create_async_engine(url, connect_args={'server_settings': {'search_path': f'{schema},public'}})
        yield async_sessionmaker(engine, expire_on_commit=False), RedisStreamBus(client)
    finally:
        await client.aclose()
        if engine:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def insert_report(factory, item):
    values = item.model_dump()
    values['source_url'] = str(item.source_url)
    async with factory() as session, session.begin():
        await session.execute(text('''
            INSERT INTO raw_reports(report_id, source_name, source_url, title, raw_text, published_at, ingested_at)
            VALUES (:report_id, :source_name, :source_url, :title, :raw_text, :published_at, :ingested_at)
        '''), values)
