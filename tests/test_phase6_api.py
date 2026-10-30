# @file: tests/test_phase6_api.py
# @exports: Scout and assistant API contracts, validation, errors and live service integration
# @depends_on: backend/main.py, tests/phase6_support.py
# @purpose: Verify HTTP routes end-to-end, response shape, fixture control and dependency recovery.
# ruff: noqa: F811
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from backend.assistant.service import AssistantService
from backend.main import app
from backend.schemas.assistant import ParameterizedSQLResponse, QueryParameters
from backend.schemas.spatial import RouteScoutResponse
from backend.scout.service import ScoutService
from tests.phase6_support import NOW, golden, insert_chokes, insert_cluster, phase6_db  # noqa: F401, F811


@pytest.mark.asyncio
@pytest.mark.parametrize('service,path,payload,result', [
    ('scout', '/api/v1/scout/evaluate', {'origin': [77, 30], 'destination': [77.1, 30]},
        RouteScoutResponse(distance_meters=1000, duration_seconds=60, advisory='Ready')),
    ('assistant', '/api/v1/assistant/query', {'query': 'Show incidents'},
        ParameterizedSQLResponse(template_id='congestion_hotspots', parameters=QueryParameters(), explanation='Bound'))])
async def test_new_api_success_validation_and_dependency_errors(service, path, payload, result):
    method = AsyncMock(return_value=result)
    state = SimpleNamespace(**{'evaluate' if service == 'scout' else 'query': method})
    with patch.object(app.state, service, state, create=True):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            assert (await client.post(path, json=payload)).status_code == 200
            assert (await client.post(path, json={**payload, 'raw_sql': 'DROP TABLE raw_reports'})).status_code == 422
            method.side_effect = ValueError('Unresolved or invalid slots')
            assert (await client.post(path, json=payload)).status_code == 422
            method.side_effect = OperationalError('statement', {}, Exception('private details'))
            response = await client.post(path, json=payload)
            assert response.status_code == 503 and 'private details' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('path,payload', [
    ('/api/v1/scout/evaluate', {'origin': [181, 30], 'destination': [77, 30]}),
    ('/api/v1/scout/evaluate', {'origin': [77, 30], 'destination': [78, 30], 'max_choke_points': 6}),
    ('/api/v1/assistant/query', {'query': ''})])
async def test_http_bounds(path, payload):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        assert (await client.post(path, json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_phase6_http_with_real_postgis_and_local_landmarks(phase6_db):
    db = phase6_db
    record = golden(longitude=77.05)
    await insert_cluster(db.factory, record)
    await insert_chokes(db.factory)
    from sqlalchemy import text
    async with db.factory() as session, session.begin():
        await session.execute(text('''INSERT INTO gazetteer_places
            (geoname_id,name,ascii_name,geom,city_name)
            VALUES (1,'Clock Tower','clock tower',ST_SetSRID(ST_MakePoint(77.05,30),4326),'Dehradun'),
                   (2,'Laxman Jhula','laxman jhula',ST_SetSRID(ST_MakePoint(77.12,30),4326),'Dehradun')'''))
    with patch.object(app.state, 'scout', ScoutService(db.factory, db.redis, db.settings), create=True), patch.object(
            app.state, 'assistant', AssistantService(db.factory, db.settings), create=True):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.post('/api/v1/scout/evaluate', json={'origin': 'Clock Tower',
                'destination': 'Laxman Jhula', 'departure_at': NOW.isoformat(), 'include_fixtures': True})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['route_source'] == 'corridor_waypoint_fallback'
            assert data['route_geojson']['type'] == 'LineString' and len(data['choke_points']) == 5
            assert data['incidents'][0]['overlap'] == 'BLOCKED_AT_ETA'
            response = await client.post('/api/v1/assistant/query', json={
                'query': 'Show severe landslides on NH-7 in the last 6 hours'})
            assert response.status_code == 200, response.text
            assert response.json()['template_id'] == 'congestion_hotspots'
            malicious = await client.post('/api/v1/assistant/query', json={
                'query': "NH-7'; DROP TABLE raw_reports; --"})
            assert malicious.status_code == 422
