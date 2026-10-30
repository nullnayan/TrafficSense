# @file: tests/test_phase6_database.py
# @exports: Phase 6 isolated PostGIS and read-only safety integration tests
# @depends_on: tests/phase6_support.py, backend/scout, backend/assistant
# @purpose: Verify actual metric SQL, fixture/cache isolation, every template and schema preservation.
# ruff: noqa: F811
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.assistant.sql_templates import execute_template
from backend.schemas.assistant import QueryParameters, SlotFillingQuery
from backend.schemas.enums import QueryIntent
from backend.schemas.spatial import RouteScoutRequest
from backend.scout.evaluator import route_incidents
from backend.scout.osrm import route_geometry
from backend.scout.service import ScoutService
from backend.scout.topology import corridor_topology
from tests.phase6_support import NOW, golden, insert_chokes, insert_cluster, phase6_db  # noqa: F401, F811


@pytest.mark.asyncio
async def test_metric_route_cache_cap_fixture_scope_and_polygon_intersection(phase6_db):
    db = phase6_db
    await insert_chokes(db.factory)
    live, fixture = golden(longitude=77.05), golden(None, None, longitude=77.07, scope='fixture')
    # Centroid is remote; an impact polygon still intersects the route.
    polygon = {'type': 'Polygon', 'coordinates': [[[77.04, 29.999], [77.06, 29.999],
        [77.06, 30.001], [77.04, 30.001], [77.04, 29.999]]]}
    remote = golden(longitude=77.5)
    await insert_cluster(db.factory, live)
    await insert_cluster(db.factory, fixture)
    await insert_cluster(db.factory, remote, polygon)
    request = RouteScoutRequest(origin=(77, 30), destination=(77.12, 30), departure_at=NOW, include_fixtures=True)
    async with db.factory() as session:
        geometry = await route_geometry(session, request, db.settings)
        assert geometry.source == 'corridor_waypoint_fallback' and len(geometry.coordinates) > 80
        assert {r.cluster_id for r in await route_incidents(session, geometry, False)} == {live.cluster_id, remote.cluster_id}
        topology, hit = await corridor_topology(session, db.redis, geometry, db.settings, True)
        assert len(topology) == 10 and not hit
        assert (await corridor_topology(session, db.redis, geometry, db.settings, True))[1]
        assert await corridor_topology(session, db.redis, geometry, db.settings, False) == ([], False)
    result = await ScoutService(db.factory, db.redis, db.settings).evaluate(request)
    assert len(result.choke_points) == 5 and len(result.incidents) == 3
    assert result.adjusted_eta >= result.baseline_eta and result.risk_level == 'HIGH'
    assert next(h for h in result.incidents if h.cluster_id == fixture.cluster_id).golden_record['waterfall_breakdown']
    # Static cache contents must not freeze active incident risk.
    async with db.factory() as session, session.begin():
        await session.execute(text('UPDATE incident_clusters SET is_active=false'))
    refreshed = await ScoutService(db.factory, db.redis, db.settings).evaluate(request)
    assert refreshed.topology_cache_hit and refreshed.incidents == []


@pytest.mark.asyncio
@pytest.mark.parametrize('intent', list(QueryIntent))
async def test_actual_templates_bound_injection_readonly_and_scope(phase6_db, intent):
    db = phase6_db
    await insert_cluster(db.factory, golden(longitude=77.05))
    await insert_cluster(db.factory, golden(longitude=77.05, scope='fixture'))
    async with db.factory() as session, session.begin():
        await session.execute(text('''INSERT INTO corridor_history
            (corridor_name,incident_type,severity,observed_clearance_mins,geom,recorded_at,source_scope)
            VALUES ('NH-7','landslide','severe',60,ST_SetSRID(ST_MakePoint(77.05,30),4326),:now,'live'),
                   ('NH-7','landslide','severe',120,ST_SetSRID(ST_MakePoint(77.05,30),4326),:now,'fixture')'''), {'now': NOW})
        await session.execute(text('''INSERT INTO gazetteer_places
            (geoname_id,name,ascii_name,geom,city_name)
            VALUES (1,'Clock Tower','clock tower',ST_SetSRID(ST_MakePoint(77.05,30),4326),'Dehradun')'''))
    slots = QueryParameters(corridor_name='NH 7', city='Dehradun', incident_type='landslide', severity='severe',
                            latitude=30, longitude=77.05, limit=1)
    query = SlotFillingQuery(intent=intent, slots=slots)
    async with db.factory() as session:
        rows = await execute_template(session, query, False, NOW)
        assert len(rows) == 1
        if intent == QueryIntent.AVERAGE_CLEARANCE:
            assert rows[0]['sample_count'] == 2 and rows[0]['average_clearance_minutes'] == 60
            assert not rows[0]['includes_fixtures']
        else:
            assert rows[0]['source_scope'] == 'live'
        slots.corridor_name = "NH-7'; DROP TABLE raw_reports; --"
        assert await execute_template(session, query, True, NOW) == []
        assert (await session.execute(text("SELECT to_regclass('raw_reports')"))).scalar_one() is not None
        await session.rollback()
        async with session.begin():
            await session.execute(text('SET TRANSACTION READ ONLY'))
            assert (await session.execute(text("SHOW transaction_read_only"))).scalar_one() == 'on'
            with pytest.raises(DBAPIError):
                await session.execute(text("DELETE FROM raw_reports"))


@pytest.mark.asyncio
async def test_metric_500m_boundary_and_helper_indexes(phase6_db):
    db = phase6_db
    record = golden(longitude=77.05)
    await insert_cluster(db.factory, record)
    async with db.factory() as session, session.begin():
        await session.execute(text('''UPDATE incident_clusters SET centroid_geom=
            ST_Project(ST_SetSRID(ST_MakePoint(77.05,30),4326)::geography,499,0)::geometry,
            impact_polygon_geom=NULL'''))
    request = RouteScoutRequest(origin=(77, 30), destination=(77.1, 30))
    async with db.factory() as session:
        geometry = await route_geometry(session, request, db.settings)
        assert len(await route_incidents(session, geometry)) == 1
        await session.execute(text('''UPDATE incident_clusters SET centroid_geom=
            ST_Project(ST_SetSRID(ST_MakePoint(77.05,30),4326)::geography,510,0)::geometry'''))
        assert await route_incidents(session, geometry) == []
        indexes = (await session.execute(text('''SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()
            AND indexname LIKE 'scout_%' '''))).scalars().all()
        assert len(indexes) == 3
        assert json.dumps(geometry.geojson)
