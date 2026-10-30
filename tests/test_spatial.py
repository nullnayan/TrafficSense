# @file: tests/test_spatial.py
# @exports: Gazetteer parsing, normalization, confidence, rate limiting and synthesis unit tests
# @depends_on: backend/spatial
# @purpose: Deterministic offline checks for spatial contracts without live geocoding requests.
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fakeredis.aioredis import FakeRedis

from backend.core.config import get_settings
from backend.schemas.extraction import ExtractedIncidentSchema, LandmarkTuple
from backend.spatial.gazetteer_loader import read_records
from backend.spatial.geocoder import Geocoder, candidate_score
from backend.spatial.nominatim import NominatimClient, RequestLimiter, learned_id
from backend.spatial.normalization import normalize, query_candidates
from backend.spatial.synthesis import synthesize


@pytest.mark.parametrize('value,expected', [('Nr Clock Twr Rajpur Rd', 'near clock tower rajpur road'),
    ('Laxman Jhula NH7 Rishikesh', 'laxman jhula nh 7 rishikesh')])
def test_abbreviation_normalization(value, expected):
    assert normalize(value) == expected
    assert query_candidates(LandmarkTuple(primary_landmark=value))


def test_geonames_19_column_and_csv_parsing(tmp_path):
    fields = ['123','Clock Tower','Clock Tower','Ghanta Ghar','30.3256','78.0418','S','TOWR',
              'IN','','39','05','','','0','','0','Asia/Kolkata','2026-01-01']
    path = tmp_path/'IN.txt'
    path.write_text('\t'.join(fields)+'\n', encoding='utf-8')
    record = list(read_records(path, {'IN.39': 'Uttarakhand'}, {'IN.39.05': 'Dehradun'}))[0]
    assert record['id'] == 123 and record['lon'] == 78.0418
    assert record['state'] == 'Uttarakhand' and record['district'] == 'Dehradun'
    path = tmp_path/'places.csv'
    path.write_text('geoname_id,name,latitude,longitude\n124,Bridge,30,78\n125,Bad,99,78\n')
    assert [bool(row) for row in read_records(path)] == [True, False]


def test_distinctive_anchor_ranks_above_road_context():
    landmark = LandmarkTuple(primary_landmark='Nr Clock Twr Chowk Rajpur Rd Dehradun')
    queries = query_candidates(landmark)
    base = {'cache_key': None, 'alternate_names': []}
    clock = candidate_score({**base, 'name': 'Dehradun Clock Tower', 'ascii_name': 'Dehradun Clock Tower'}, queries, '')
    road = candidate_score({**base, 'name': 'Rajpur Road', 'ascii_name': 'Rajpur Road'}, queries, '')
    assert 0.65 <= clock <= 1 and clock > road


@pytest.mark.asyncio
async def test_one_hertz_nominatim_and_write_through():
    clock = [0.0]
    async def sleep(seconds):
        clock[0] += seconds
    limiter = RequestLimiter(clock=lambda: clock[0], sleep=sleep)
    settings = get_settings().model_copy(update={'nominatim_enabled': True})
    times, requests = [], []
    def handler(request):
        times.append(clock[0])
        requests.append(request)
        return httpx.Response(200, json=[{'lat': '30.32', 'lon': '78.04', 'display_name': 'Test Bridge',
            'address': {'country_code': 'in', 'state': 'Uttarakhand', 'city': 'Dehradun'}}])
    session = SimpleNamespace(execute=AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fallback = NominatimClient(settings, client, limiter)
        for key in ('new bridge 1', 'new bridge 2'):
            result = await fallback.lookup(session, LandmarkTuple(primary_landmark='Test Bridge',
                city='Dehradun', state='Uttarakhand'), key)
            assert result.geoname_id == learned_id(key) < -1_000_000_000_000
    assert times == [0.0, 1.0]
    assert requests[0].headers['User-Agent'] == settings.nominatim_user_agent
    assert session.execute.await_count == 2 and 'INSERT INTO gazetteer_places' in str(session.execute.call_args.args[0])


@pytest.mark.asyncio
async def test_no_point_fabricated_from_corridor_only():
    session = AsyncMock()
    assert await Geocoder().geocode(session, LandmarkTuple(corridor_name='NH-7', city='Dehradun')) is None
    session.execute.assert_not_awaited()


def test_partial_lane_evidence_increases_completeness():
    rows = []
    for blocked in (None, 2):
        identity = uuid4()
        attributes = ExtractedIncidentSchema(report_id=identity, incident_type='landslide', temporal_status='active',
            landmark_tuple=LandmarkTuple(primary_landmark='Kali Mandir', city='Dehradun'), lanes_blocked=blocked)
        rows.append({'report_id': identity, 'extracted_attributes': attributes.model_dump(mode='json'),
                     'setfit_confidence': 0.9, 'geocode_confidence': 0.9,
                     'published_at': datetime(2026, 10, 1, tzinfo=timezone.utc)})
    first, old_score = synthesize(rows[:1])
    merged, new_score = synthesize(rows)
    assert first.lanes_blocked is None and merged.lanes_blocked == 2
    assert merged.total_lanes is None and 0 <= old_score < new_score <= 1
    rows[1]['extracted_attributes']['extraction_confidence'] = 0.0
    assert synthesize(rows)[0].lanes_blocked is None


@pytest.mark.asyncio
async def test_redis_gate_throttles_independent_limiter_instances():
    import asyncio
    import time
    starts = []
    async with FakeRedis(decode_responses=True) as redis:
        async def request(limiter):
            async with limiter.lock:
                await limiter.wait()
                starts.append(time.monotonic())
        await asyncio.gather(request(RequestLimiter(redis)), request(RequestLimiter(redis)))
    assert sorted(starts)[1]-sorted(starts)[0] >= 1.0
