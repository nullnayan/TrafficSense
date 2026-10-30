# @file: backend/scout/osrm.py
# @exports: resolve_endpoint, route_geometry
# @depends_on: backend/scout/geometry.py, backend/spatial/geocoder.py, httpx
# @purpose: Local endpoint resolution and bounded opt-in OSRM routing with labeled offline fallback.
import httpx

from backend.schemas.extraction import LandmarkTuple
from backend.schemas.spatial import GeocodedLocation
from backend.scout.geometry import RouteGeometry, fallback_geometry
from backend.spatial.geocoder import Geocoder


async def resolve_endpoint(session, endpoint, settings, geocoder=None):
    if isinstance(endpoint, GeocodedLocation):
        return endpoint
    if isinstance(endpoint, (tuple, list)):
        return GeocodedLocation(longitude=endpoint[0], latitude=endpoint[1], method='provided', confidence=1)
    # Local gazetteer only: scout and assistant resolution never need a provider key.
    local = settings.model_copy(update={'nominatim_enabled': False})
    result = await (geocoder or Geocoder(local)).geocode(session, LandmarkTuple(primary_landmark=endpoint))
    if result is None:
        raise ValueError(f'Could not resolve landmark: {endpoint}')
    return result


async def _live_route(client, origin, destination, settings):
    path = f'/route/v1/driving/{origin.longitude},{origin.latitude};{destination.longitude},{destination.latitude}'
    for _ in range(settings.osrm_max_retries+1):
        try:
            response = await client.get(settings.osrm_base_url.rstrip('/')+path,
                params={'overview': 'full', 'geometries': 'geojson'}, timeout=settings.osrm_timeout_seconds)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError('OSRM payload exceeds limit')
            payload = response.json()
            if payload.get('code') != 'Ok':
                raise ValueError('No OSRM route')
            route = payload['routes'][0]
            if route['geometry']['type'] != 'LineString':
                raise ValueError('OSRM geometry must be a LineString')
            return RouteGeometry(route['geometry']['coordinates'], float(route['duration']), 'osrm_live')
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            continue
    return None


async def route_geometry(session, request, settings, client=None, geocoder=None):
    origin = await resolve_endpoint(session, request.origin, settings, geocoder)
    destination = await resolve_endpoint(session, request.destination, settings, geocoder)
    live = None
    if settings.osrm_enabled and settings.ingestion_source_mode != 'fixtures':
        if client is not None:
            live = await _live_route(client, origin, destination, settings)
        else:
            async with httpx.AsyncClient() as owned:
                live = await _live_route(owned, origin, destination, settings)
    return live or await fallback_geometry(session, origin, destination, settings,
                                           request.include_fixtures, request.corridor_name)
