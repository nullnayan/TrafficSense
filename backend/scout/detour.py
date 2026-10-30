# @file: backend/scout/detour.py
# @exports: blockade_detour
# @depends_on: PostGIS, httpx, backend/scout/geometry.py
# @purpose: Authenticated ORS polygon-avoidance candidate with PostGIS validation and graceful fallback.
import asyncio
import json

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.scout.geometry import RouteGeometry, distance

BUFFER = text('''
    SELECT ST_AsGeoJSON(ST_UnaryUnion(ST_Collect(COALESCE(impact_polygon_geom,
        ST_Buffer(centroid_geom::geography,500)::geometry)))) AS polygon
    FROM incident_clusters WHERE cluster_id=ANY(CAST(:ids AS uuid[])) AND source_scope='live'
''')
VERIFY = text('''SELECT ST_IsValid(ST_GeomFromGeoJSON(:route)) AND
    NOT ST_Intersects(ST_GeomFromGeoJSON(:route),ST_GeomFromGeoJSON(:polygon))''')


async def blockade_detour(session, route, hazards, settings, include_fixtures=False, client=None):
    key = settings.ors_api_key.get_secret_value().strip()
    blocked = [str(h.cluster_id) for h in hazards if h.overlap == 'BLOCKED_AT_ETA']
    if not key or include_fixtures or settings.ingestion_source_mode == 'fixtures' or not blocked:
        return {'detour_status': 'not_requested'}
    try:
        polygon = (await session.execute(BUFFER, {'ids': blocked})).scalar_one_or_none()
        polygon = json.loads(polygon) if isinstance(polygon, str) else polygon
        if not polygon or polygon.get('type') not in ('Polygon', 'MultiPolygon'):
            return {'detour_status': 'no_avoidance_polygon'}
        if client is None:
            async with httpx.AsyncClient(timeout=6) as owned:
                return await _request(session, route, polygon, key, owned)
        return await _request(session, route, polygon, key, client)
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, IndexError, TypeError, SQLAlchemyError):
        return {'detour_status': 'unavailable'}


async def _request(session, route, polygon, key, client):
    response = await asyncio.wait_for(client.post(
        'https://api.openrouteservice.org/v2/directions/driving-car/geojson',
        headers={'Authorization': key}, json={'coordinates': [route.coordinates[0], route.coordinates[-1]],
        'options': {'avoid_polygons': polygon}, 'instructions': False}, timeout=6), 6)
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError('ORS payload exceeds limit')
    feature = response.json()['features'][0]
    if feature['geometry']['type'] != 'LineString':
        raise ValueError('Invalid ORS route type')
    candidate = RouteGeometry(feature['geometry']['coordinates'], feature['properties']['summary']['duration'], 'ors_live')
    if any(distance(a, b) > 500 for a, b in zip(
            [candidate.coordinates[0], candidate.coordinates[-1]], [route.coordinates[0], route.coordinates[-1]])):
        raise ValueError('ORS endpoints differ from requested journey')
    valid = (await session.execute(VERIFY, {'route': json.dumps(candidate.geojson),
                                          'polygon': json.dumps(polygon)})).scalar_one()
    if not valid:
        return {'detour_status': 'intersects_blockade'}
    return {'detour_status': 'available', 'detour_route_geojson': candidate.geojson,
            'detour_duration_seconds': candidate.duration_seconds, 'detour_source': 'ors_live'}
