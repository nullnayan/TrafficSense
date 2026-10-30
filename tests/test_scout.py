# @file: tests/test_scout.py
# @exports: Geometry, overlap, cap, OSRM and topology cache regressions
# @depends_on: backend/scout, tests/phase6_support.py
# @purpose: Verify numerical arrival boundaries and bounded resilient offline/live routing behavior.
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fakeredis.aioredis import FakeRedis
from redis.exceptions import ConnectionError

from backend.core.config import Settings
from backend.schemas.spatial import RouteScoutRequest
from backend.scout.evaluator import evaluate_overlap
from backend.scout.geometry import RouteGeometry, distance, segmented
from backend.scout.osrm import resolve_endpoint, route_geometry
from backend.scout.topology import corridor_topology, rank_chokes, sample_chokes, sample_traffic_chokes
from tests.phase6_support import NOW, golden


def session_rows(rows):
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    session.execute.return_value = result
    return session


def route():
    coordinates = segmented([[77, 30], [78, 30]])
    total = sum(distance(a, b) for a, b in zip(coordinates, coordinates[1:]))
    return RouteGeometry(coordinates, total/1000*60, 'corridor_waypoint_fallback')


@pytest.mark.parametrize('expected,upper,classification', [
    (60, 90, 'BLOCKED_AT_ETA'), (20, 90, 'RESIDUAL_RISK'), (10, 20, 'CLEARED_BEFORE_ETA')])
def test_overlap_all_three_cases(expected, upper, classification):
    geometry = route()
    record = golden(expected, upper)
    result = evaluate_overlap(record, geometry, NOW)
    assert result.overlap == classification
    assert result.arrival_at == NOW+timedelta(minutes=result.travel_minutes_to_incident)
    assert (result.delay_minutes == 0) == (classification == 'CLEARED_BEFORE_ETA')


@pytest.mark.parametrize('offset,classification', [(0, 'BLOCKED_AT_ETA'), (30, 'RESIDUAL_RISK'), (30.01, 'CLEARED_BEFORE_ETA')])
def test_expected_and_upper_bound_inclusive(offset, classification):
    record, geometry = golden(60, 90), route()
    travel = geometry.travel_minutes(geometry.project([77.5, 30])[0])
    departure = NOW+timedelta(minutes=60-travel+offset)
    assert evaluate_overlap(record, geometry, departure).overlap == classification


def test_kilometer_45_cleared_twenty_minutes_before_arrival():
    geometry = RouteGeometry([[77, 0], [78, 0]], distance([77, 0], [78, 0])/1000*60, 'osrm_live')
    record = golden(15, 25, longitude=77+45000/geometry.distance_meters)
    record.location.latitude = 0
    result = evaluate_overlap(record, geometry, NOW)
    assert result.distance_along_route_meters == pytest.approx(45000)
    assert result.travel_minutes_to_incident == pytest.approx(45)
    assert (result.arrival_at-result.clear_upper_at).total_seconds()/60 == pytest.approx(20)
    assert result.overlap == 'CLEARED_BEFORE_ETA' and result.delay_minutes == 0


def test_unanalyzed_hazard_gets_clearance_and_cumulative_distances():
    geometry = route()
    result = evaluate_overlap(golden(None, None), geometry, NOW)
    assert result.golden_record['waterfall_breakdown']['waterfall_level_used'] == 3
    assert result.clear_upper_at >= result.estimated_clear_at
    assert geometry.cumulative_meters == sorted(geometry.cumulative_meters)
    assert geometry.travel_minutes(geometry.distance_meters) == pytest.approx(geometry.duration_seconds/60)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'malformed', 'disabled', 'fixtures', 'success'])
async def test_osrm_bounded_retries_or_rich_fallback(failure):
    settings = Settings(osrm_enabled=failure != 'disabled', osrm_max_retries=1,
        ingestion_source_mode='fixtures' if failure == 'fixtures' else 'live')
    calls = []
    def handle(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout', request=request)
        if failure == 'malformed':
            return httpx.Response(200, json={'code': 'Ok', 'routes': [{'duration': -1}]})
        return httpx.Response(200, json={'code': 'Ok', 'routes': [{'duration': 600,
            'geometry': {'type': 'LineString', 'coordinates': [[77, 30], [77.1, 30]]}}]})
    session = session_rows([{'longitude': 77.05, 'latitude': 30.001}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        geometry = await route_geometry(session, RouteScoutRequest(origin=(77, 30), destination=(77.1, 30)), settings, client)
    assert len(calls) == (0 if failure in {'disabled', 'fixtures'} else 1 if failure == 'success' else 2)
    assert geometry.source == ('osrm_live' if failure == 'success' else 'corridor_waypoint_fallback')
    assert geometry.geojson['type'] == 'LineString' and len(geometry.coordinates) >= 2
    if failure != 'success':
        assert len(geometry.coordinates) >= 9


@pytest.mark.asyncio
async def test_local_landmark_resolution_and_unresolved_error():
    geocoder = MagicMock(geocode=AsyncMock(return_value=golden().location))
    assert await resolve_endpoint(AsyncMock(), 'Clock Tower', Settings(), geocoder) == golden().location
    geocoder.geocode.return_value = None
    with pytest.raises(ValueError, match='Could not resolve'):
        await resolve_endpoint(AsyncMock(), 'Unknown place', Settings(), geocoder)


@pytest.mark.asyncio
async def test_topology_cache_miss_hit_scope_ttl_and_outage():
    session = session_rows([])
    async with FakeRedis(decode_responses=True) as redis:
        for fixtures, hit in [(False, False), (False, True), (True, False)]:
            _, cached = await corridor_topology(session, redis, route(), Settings(), fixtures)
            assert cached is hit
        assert session.execute.await_count == 2
        assert all(0 < ttl <= 300 for ttl in [await redis.ttl(key) for key in await redis.keys('ts:scout:*')])
    failed = AsyncMock()
    failed.get.side_effect = failed.set.side_effect = ConnectionError('offline')
    assert (await corridor_topology(session, failed, route(), Settings())) == ([], False)


@pytest.mark.parametrize('cap', [3, 4, 5])
def test_risk_ranking_caps_more_than_eight_candidates(cap):
    rows = [{'choke_id': str(uuid4()), 'choke_name': str(i), 'longitude': 77.1+i*0.01,
             'latitude': 30, 'fraction': i/10, 'base_delay_minutes': i+1, 'peak_multiplier': 2} for i in range(10)]
    selected = rank_chokes(rows, [], cap)
    assert len(sample_chokes(selected, [], route())) == cap
    assert [r['choke_name'] for r in selected] == [str(i) for i in range(10-cap, 10)]
    hazard = evaluate_overlap(golden(600, 700, longitude=77.1), route(), NOW)
    assert rank_chokes(rows, [hazard], cap)[0]['choke_name'] == '0'


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['tomtom', 'here'])
@pytest.mark.parametrize('cap', [3, 4, 5])
@pytest.mark.parametrize('failure', [False, True])
async def test_live_traffic_sampling_cap_ratio_cache_and_fallback(provider, cap, failure):
    rows = [{'choke_id': str(uuid4()), 'choke_name': str(i), 'longitude': 77.1+i*.01,
             'latitude': 30, 'fraction': i/10, 'base_delay_minutes': i+1, 'peak_multiplier': 1}
            for i in range(10)]
    settings = Settings(scout_live_traffic_enabled=True, ingestion_source_mode='live',
        tomtom_api_key='mock-key' if provider == 'tomtom' else '',
        here_api_key='mock-key' if provider == 'here' else '')
    calls = []
    def handle(request):
        calls.append(request)
        assert all(value == 2.5 for value in request.extensions['timeout'].values())
        if failure:
            raise httpx.ReadTimeout('mock timeout', request=request)
        payload = ({'flowSegmentData': {'currentSpeed': 20, 'freeFlowSpeed': 40}} if provider == 'tomtom'
                   else {'results': [{'currentFlow': {'speed': 10, 'freeFlow': 20}}]})
        return httpx.Response(200, json=payload)
    async with FakeRedis(decode_responses=True) as redis, \
            httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        ranked = rank_chokes(rows, [], cap)
        chokes = await sample_traffic_chokes(ranked, [], route(), redis, settings, client=client)
        assert len(chokes) == len(calls) == cap <= 5
        assert all(c.telemetry_source == ('local_incident_clusters' if failure else f'{provider}_live') for c in chokes)
        assert all(c.live_speed_ratio == (None if failure else .5) for c in chokes)
        if not failure:
            assert all(c.current_speed_kmh == (20 if provider == 'tomtom' else 36) for c in chokes)
            again = await sample_traffic_chokes(ranked, [], route(), redis, settings, client=client)
            assert len(calls) == cap and all(c.traffic_cache_hit for c in again)
            assert all(0 < ttl <= 60 for ttl in [await redis.ttl(k) for k in await redis.keys('ts:scout:flow:*')])
