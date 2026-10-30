# @file: tests/analytics_support.py
# @exports: golden, analytics_services, insert_cluster, envelope, add_history, add_choke
# @depends_on: sql/01_schema.sql, sql/05_spatial_pipeline.sql, sql/06_analytics_pipeline.sql
# @purpose: Independent Phase 5 records and isolated real PostGIS/fake-Redis integration fixtures.
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.core.config import ROOT, get_settings
from backend.core.redis_bus import RedisStreamBus
from backend.schemas.spatial import GoldenRecordSchema, SpatialReportEnvelope

NOW = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)


def golden(**updates):
    report_id = uuid4()
    values = dict(report_id=report_id, cluster_id=uuid4(), incident_type='collision', severity='moderate',
        landmark_tuple={'corridor_name': 'NH-58'}, report_count=1, source_report_ids=[report_id],
        attribute_completeness_score=0.5, first_reported_at=NOW, last_updated_at=NOW, last_reported_at=NOW,
        source_scope='fixture', location={'longitude': 77.0, 'latitude': 30.0,
        'method': 'provided', 'confidence': 1})
    return GoldenRecordSchema.model_validate({**values, **updates})


@pytest_asyncio.fixture
async def analytics_services():
    if os.getenv('TRAFFICSENSE_INTEGRATION') != '1':
        pytest.skip('Set TRAFFICSENSE_INTEGRATION=1 for isolated PostGIS tests')
    url = get_settings().database_url.get_secret_value()
    schema = 'analytics_test_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': f'{schema},public'}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.connect() as connection:
            driver = (await connection.get_raw_connection()).driver_connection
            for name in ('01_schema.sql', '05_spatial_pipeline.sql', '06_analytics_pipeline.sql'):
                await driver.execute((ROOT / 'sql' / name).read_text(encoding='utf-8'))
        async with FakeRedis(decode_responses=True) as client:
            yield factory, RedisStreamBus(client)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


async def insert_cluster(factory, record=None):
    record = record or golden()
    async with factory() as session, session.begin():
        await session.execute(text('''INSERT INTO incident_clusters
            (cluster_id,incident_type,severity,temporal_status,canonical_attributes,source_report_ids,
             first_reported_at,last_updated_at,last_reported_at,centroid_geom,source_scope,
             report_count,attribute_completeness_score,geocode_confidence,corridor_name)
            VALUES (:id,:kind,:severity,'active',CAST(:attrs AS jsonb),CAST(:ids AS uuid[]),
                :at,:at,:at,ST_SetSRID(ST_MakePoint(:lon,:lat),4326),:scope,1,0.5,1,:road)'''),
            {'id': record.cluster_id, 'kind': str(record.incident_type), 'severity': str(record.severity),
             'attrs': json.dumps({name: value for name, value in record.model_dump(mode='json').items()
                                 if name in record.__class__.__bases__[0].model_fields}),
             'ids': record.source_report_ids, 'at': record.last_reported_at, 'scope': record.source_scope,
             'lon': record.location.longitude, 'lat': record.location.latitude,
             'road': record.landmark_tuple.corridor_name})
        await session.execute(text('''INSERT INTO raw_reports
            (report_id,source_name,source_url,title,raw_text,published_at,cluster_id,pipeline_status)
            VALUES (:id,'fixture:analytics',:url,'test','test',:at,:cluster,'CLUSTERED')'''),
            {'id': record.report_id, 'url': f'https://example.test/{record.report_id}',
             'at': record.last_reported_at, 'cluster': record.cluster_id})
    return record


def envelope(record, revision=1, superseded=()):
    return SpatialReportEnvelope(report_id=record.report_id, cluster_id=record.cluster_id,
        cluster_revision=revision, geocoded_location=record.location, golden_record=record,
        superseded_cluster_ids=list(superseded))


async def add_history(session, minutes=60, road='NH-58', offset=0, scope='fixture', kind='collision'):
    await session.execute(text('''INSERT INTO corridor_history
        (corridor_name,incident_type,severity,observed_clearance_mins,geom,source_scope)
        VALUES (:road,:kind,'moderate',:minutes,
            ST_Project(ST_SetSRID(ST_MakePoint(77,30),4326)::geography,
                CAST(:offset AS double precision),0::double precision)::geometry,:scope)'''),
        {'road': road, 'kind': kind, 'minutes': minutes, 'offset': offset, 'scope': scope})


async def add_choke(session, offset=0, scope='fixture'):
    await session.execute(text('''INSERT INTO corridor_choke_points
        (corridor_name,choke_name,geom,source_scope,base_delay_minutes,peak_multiplier,choke_radius_meters)
        VALUES ('NH-58','reference',ST_Project(ST_SetSRID(ST_MakePoint(77,30),4326)::geography,
            CAST(:offset AS double precision),0::double precision)::geometry,:scope,20,2,500)'''),
        {'offset': offset, 'scope': scope})
