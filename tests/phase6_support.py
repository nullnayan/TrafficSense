# @file: tests/phase6_support.py
# @exports: NOW, golden, phase6_db, insert_cluster, insert_chokes
# @depends_on: asyncpg, PostGIS, Redis, backend/schemas/spatial.py
# @purpose: Independent Phase 6 fixtures with isolated schemas and no protected test-source imports.
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.core.config import ROOT, Settings
from backend.schemas.spatial import GoldenRecordSchema

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def golden(expected=60, upper=90, longitude=77.5, scope='live'):
    report = uuid4()
    return GoldenRecordSchema(report_id=report, incident_type='landslide', severity='severe',
        temporal_status='active', landmark_tuple={'primary_landmark': 'Clock Tower', 'corridor_name': 'NH-7',
                                               'city': 'Dehradun'},
        cluster_id=uuid4(), source_report_ids=[report], report_count=1, attribute_completeness_score=0.8,
        location={'longitude': longitude, 'latitude': 30, 'method': 'provided', 'confidence': 1},
        first_reported_at=NOW-timedelta(minutes=5), last_updated_at=NOW, last_reported_at=NOW,
        expected_clearance_minutes=expected, clearance_upper_bound_minutes=upper,
        estimated_clear_at=NOW+timedelta(minutes=expected) if expected is not None else None,
        waterfall_breakdown={'waterfall_level_used': 3, 'estimated_clearance_mins': expected,
            'bayesian_bound_mins': upper, 'hard_ceiling_mins': 720, 'provenance': 'synthetic Phase 6 test'}
            if expected is not None else None, source_scope=scope)


@pytest_asyncio.fixture
async def phase6_db():
    import pytest
    if os.getenv('TRAFFICSENSE_INTEGRATION') != '1':
        pytest.skip('Set TRAFFICSENSE_INTEGRATION=1 for isolated PostGIS integration')
    settings = Settings(osrm_enabled=False, ingestion_source_mode='fixtures')
    schema = 'phase6_'+uuid4().hex
    connection = await asyncpg.connect(settings.database_url.get_secret_value().replace('+asyncpg', ''), timeout=10)
    engine = None
    redis = FakeRedis(decode_responses=True)
    try:
        await connection.execute(f'CREATE SCHEMA {schema}; SET search_path TO {schema},public')
        for name in ('01_schema.sql', '03_ingestion_outbox.sql', '04_nlp_states.sql',
                     '05_spatial_pipeline.sql', '06_analytics_pipeline.sql', '07_scout_assistant.sql'):
            await connection.execute((ROOT/'sql'/name).read_text(encoding='utf-8'))
        engine = create_async_engine(settings.database_url.get_secret_value(),
            connect_args={'server_settings': {'search_path': f'{schema},public'}, 'timeout': 10})
        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield SimpleNamespace(factory=factory, engine=engine, redis=redis, settings=settings)
    finally:
        if engine:
            await engine.dispose()
        await redis.aclose()
        await connection.execute(f'SET search_path TO public; DROP SCHEMA {schema} CASCADE')
        await connection.close()


async def insert_cluster(factory, record, polygon=None):
    async with factory() as session, session.begin():
        await session.execute(text('''INSERT INTO incident_clusters (
            cluster_id,incident_type,severity,temporal_status,corridor_name,primary_landmark,city,
            source_report_ids,report_count,attribute_completeness_score,geocode_confidence,
            centroid_geom,impact_polygon_geom,canonical_attributes,first_reported_at,last_updated_at,
            last_reported_at,expected_clearance_minutes,clearance_upper_bound_minutes,
            waterfall_breakdown,estimated_clear_at,source_scope)
            VALUES (:id,:kind,:severity,'active','NH-7','Clock Tower','Dehradun',:reports,1,0.8,1,
                ST_SetSRID(ST_MakePoint(:lon,30),4326),
                ST_SetSRID(ST_GeomFromGeoJSON(:polygon),4326),CAST(:attrs AS jsonb),
                :first,:updated,:updated,:expected,:upper,CAST(:breakdown AS jsonb),:clear,:scope)'''),
            {'id': record.cluster_id, 'kind': record.incident_type.value, 'severity': record.severity.value,
             'reports': record.source_report_ids, 'lon': record.location.longitude,
             'polygon': json.dumps(polygon) if polygon else None, 'attrs': record.model_dump_json(),
             'first': record.first_reported_at, 'updated': record.last_updated_at,
             'expected': record.expected_clearance_minutes, 'upper': record.clearance_upper_bound_minutes,
             'breakdown': record.waterfall_breakdown.model_dump_json() if record.waterfall_breakdown else None,
             'clear': record.estimated_clear_at, 'scope': record.source_scope})


async def insert_chokes(factory, count=10, scope='fixture'):
    async with factory() as session, session.begin():
        for index in range(count):
            await session.execute(text('''INSERT INTO corridor_choke_points
                (choke_id,corridor_name,choke_name,static_topology_score,base_delay_minutes,
                 peak_multiplier,choke_radius_meters,source_scope,geom)
                VALUES (:id,'NH-7',:name,0.6,:delay,1.5,500,:scope,
                        ST_SetSRID(ST_MakePoint(:lon,30),4326))'''),
                {'id': uuid4(), 'name': f'Choke {index}', 'delay': index+1, 'scope': scope,
                 'lon': 77+0.01*(index+1)})
