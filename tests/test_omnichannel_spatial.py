# @file: tests/test_omnichannel_spatial.py
# @exports: Tier-2 geocode, flow confidence, weather context and detour contract tests
# @depends_on: backend/spatial/geoapify.py, backend/scout/{detour,weather,traffic_provider}.py
# @purpose: Verify local cache writes, India restrictions, buffered avoidance and graceful provider failure.
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.core.config import Settings
from backend.schemas.extraction import LandmarkTuple
from backend.scout.detour import blockade_detour
from backend.scout.traffic_provider import sample_live_traffic
from backend.scout.weather import fallback_weather
from backend.spatial.geoapify import GeoapifyClient
from backend.spatial.geocoder import Geocoder
from tests.test_scout import route
from tests.test_traffic_provider import chokes


@pytest.mark.asyncio
async def test_geocoder_uses_geoapify_between_local_and_nominatim():
    geocoder = Geocoder(Settings(_env_file=None, geoapify_api_key='test'))
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        mappings=lambda: SimpleNamespace(all=lambda: []))))
    landmark = LandmarkTuple(primary_landmark='Unknown Lane', city='Dehradun')
    geocoded = chokes()[0].location.model_copy(update={'method': 'geoapify'})
    with patch.object(geocoder.tier_two, 'lookup', new=AsyncMock(return_value=geocoded)) as tier_two, \
         patch.object(geocoder.fallback, 'lookup', new=AsyncMock()) as nominatim:
        assert await geocoder.geocode(session, landmark) == geocoded
        tier_two.assert_awaited_once()
        nominatim.assert_not_awaited()
    with patch.object(geocoder.tier_two, 'lookup', new=AsyncMock(return_value=None)), \
         patch.object(geocoder.fallback, 'lookup', new=AsyncMock(return_value=geocoded)) as nominatim:
        assert await geocoder.geocode(session, landmark) == geocoded
        nominatim.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['valid', 'foreign', 'low_confidence', 'wrong_city', 'empty', 'timeout', 'fixtures'])
async def test_geoapify_validation_and_cache(case):
    row = {'lon': 78.04, 'lat': 30.32, 'rank': {'confidence': .95}, 'country_code': 'in',
           'state': 'Uttarakhand', 'city': 'Dehradun', 'formatted': 'Example Lane, Dehradun'}
    if case == 'foreign':
        row['country_code'] = 'us'
    if case == 'low_confidence':
        row['rank']['confidence'] = .2
    if case == 'wrong_city':
        row['city'] = 'Delhi'
    requests = []
    def handler(request):
        requests.append(request)
        if case == 'timeout':
            raise httpx.ReadTimeout('slow')
        return httpx.Response(200, json={'features': [] if case == 'empty' else [{'properties': row}]})
    session = SimpleNamespace(execute=AsyncMock())
    settings = Settings(geoapify_api_key='test', ingestion_source_mode='fixtures' if case == 'fixtures' else 'live')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GeoapifyClient(settings, client).lookup(session,
            LandmarkTuple(primary_landmark='Example Lane', city='Dehradun', state='Uttarakhand'), 'example lane')
    assert bool(result) == (case == 'valid')
    if result:
        assert result.method == 'geoapify' and result.confidence == .95
        assert 'ON CONFLICT' in str(session.execute.call_args.args[0])
    else:
        session.execute.assert_not_awaited()
    if requests:
        assert requests[0].url.params['filter'] == 'countrycode:in'
    if case == 'fixtures':
        assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['success', 'intersects', 'failure', 'no_key', 'fixtures', 'cleared'])
async def test_ors_postgis_polygon_avoidance(case):
    polygon = {'type': 'Polygon', 'coordinates': [[[78, 30], [78.01, 30], [78.01, 30.01], [78, 30]]]}
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: json.dumps(polygon)),
        SimpleNamespace(scalar_one=lambda: case != 'intersects')]))
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(503) if case == 'failure' else httpx.Response(200, json={'features': [{
            'geometry': route().geojson, 'properties': {'summary': {'duration': 7000}}}]})
    settings = Settings(ors_api_key='' if case == 'no_key' else 'test', ingestion_source_mode='live')
    hazards = [SimpleNamespace(cluster_id='00000000-0000-0000-0000-000000000001',
        overlap='CLEARED_BEFORE_ETA' if case == 'cleared' else 'BLOCKED_AT_ETA')]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await blockade_detour(session, route(), hazards, settings, case == 'fixtures', client)
    assert result['detour_status'] == {'success': 'available', 'intersects': 'intersects_blockade',
        'failure': 'unavailable'}.get(case, 'not_requested')
    if case in ('no_key', 'fixtures', 'cleared'):
        assert not requests
    else:
        assert json.loads(requests[0].content)['options']['avoid_polygons'] == polygon
        assert requests[0].headers['authorization'] == 'test'
    if case == 'success':
        assert result['detour_source'] == 'ors_live' and result['detour_duration_seconds'] == 7000


@pytest.mark.asyncio
async def test_flow_confidence_and_context_only_precipitation():
    settings = Settings(scout_live_traffic_enabled=True, tomtom_api_key='test', ingestion_source_mode='live')
    def handler(request):
        if request.url.host == 'api.tomtom.com':
            return httpx.Response(200, json={'flowSegmentData': {
                'currentSpeed': 10, 'freeFlowSpeed': 40, 'confidence': .9}})
        return httpx.Response(200, json={'current': {'time': '2026-10-14T12:00',
                            'precipitation': 5, 'weather_code': 61}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        live = await sample_live_traffic(chokes(), None, settings, client=client)
        weather = await fallback_weather(chokes(), settings, client=client)
    assert all(point.traffic_confidence == .9 for point in live)
    assert all(point.weather_context['context_only'] and point.weather_context['precipitation_mm'] == 5 for point in weather)
    assert all(point.telemetry_source == 'local_incident_clusters' for point in weather)
