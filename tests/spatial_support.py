# @file: tests/spatial_support.py
# @exports: spatial_services, insert_extracted, process_all
# @depends_on: backend/spatial/pipeline.py, sql/05_spatial_pipeline.sql
# @purpose: Fresh isolated PostGIS fixtures and Redis for real metric spatial integration tests.
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.core.config import get_settings
from backend.core.redis_bus import RedisStreamBus, StreamNames
from backend.schemas.extraction import ExtractedIncidentSchema, LandmarkTuple
from backend.schemas.ingestion import utc_now
from backend.spatial.geocoder import Geocoder
from backend.spatial.pipeline import process_extracted_stream_batch


@pytest_asyncio.fixture
async def spatial_services():
    if os.environ.get('TRAFFICSENSE_INTEGRATION') != '1':
        pytest.skip('Requires PostGIS; set TRAFFICSENSE_INTEGRATION=1')
    url = get_settings().database_url.get_secret_value()
    admin = create_async_engine(url)
    schema = 'ts_spatial_test_' + uuid4().hex
    engine, client = None, FakeRedis(decode_responses=True)
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for name in ('raw_reports','incident_clusters','gazetteer_places','spatial_stage_outbox'):
                await connection.execute(text(f'CREATE TABLE "{schema}".{name} (LIKE public.{name} INCLUDING ALL)'))
            await connection.execute(text(f'''INSERT INTO "{schema}".gazetteer_places
                SELECT * FROM public.gazetteer_places WHERE geoname_id BETWEEN -10 AND -1'''))
        engine = create_async_engine(url, connect_args={'server_settings': {'search_path': f'{schema},public'}})
        yield async_sessionmaker(engine, expire_on_commit=False), RedisStreamBus(client)
    finally:
        await client.aclose()
        if engine:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def insert_extracted(factory, bus, minutes=0, metres=None, blocked=None, kind='landslide', landmark=None):
    identity = uuid4()
    attributes = ExtractedIncidentSchema(report_id=identity, incident_type=kind, temporal_status='active',
        landmark_tuple=landmark or LandmarkTuple(primary_landmark='Clock Tower', city='Dehradun', state='Uttarakhand'),
        lanes_blocked=blocked, summary_reason='SYNTHETIC spatial regression report')
    base = utc_now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    async with factory() as session, session.begin():
        await session.execute(text('''
            INSERT INTO raw_reports(report_id,source_name,source_url,title,raw_text,published_at,temporal_status,
                extracted_attributes,landmark_tuple,pipeline_status,geom,geocode_method,geocode_confidence)
            VALUES (:id,'fixture:spatial_test',:url,'Landslide on NH-7','Synthetic annotated landslide',:published,'active',
                CAST(:attributes AS jsonb),CAST(:landmark AS jsonb),:state,
                CASE WHEN :provided THEN ST_Project(ST_SetSRID(ST_MakePoint(78.0418,30.3256),4326)::geography,
                    CAST(:metres AS float8),0)::geometry ELSE NULL END,'provided',0.95)
        '''), {'id': identity, 'url': f'https://fixtures.trafficsense.invalid/spatial-test/{identity}',
               'published': base+timedelta(minutes=minutes), 'attributes': attributes.model_dump_json(),
               'landmark': attributes.landmark_tuple.model_dump_json(), 'state': 'GEOCODED' if metres is not None else 'EXTRACTED',
               'provided': metres is not None, 'metres': metres or 0})
    await bus.publish(StreamNames.EXTRACTED_ITEMS, attributes)
    return attributes


async def process_all(factory, bus, geocoder=None):
    settings = get_settings().model_copy(update={'nominatim_enabled': False})
    async with factory() as session:
        return await process_extracted_stream_batch(session, bus, geocoder or Geocoder(settings))
