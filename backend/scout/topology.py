# @file: backend/scout/topology.py
# @exports: MAX_SCOUT_CHOKE_POINTS, corridor_topology, rank_chokes, sample_chokes, sample_traffic_chokes
# @depends_on: Redis, PostGIS, backend/scout/geometry.py
# @purpose: Cache scoped topology, rank fresh incident proximity and enrich at most five opt-in traffic samples.
import hashlib
import json

from redis.exceptions import RedisError
from sqlalchemy import text

from backend.schemas.spatial import GeocodedLocation, RouteChokePoint
from backend.scout.geometry import distance
from backend.scout.traffic_provider import MAX_SCOUT_CHOKE_POINTS, sample_live_traffic

TOPOLOGY = text('''WITH route AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:route),4326) AS geom)
    SELECT c.choke_id,c.choke_name,c.corridor_name,c.static_topology_score,
        c.base_delay_minutes,c.peak_multiplier,c.choke_radius_meters,
        ST_X(c.geom) AS longitude,ST_Y(c.geom) AS latitude,
        ST_LineLocatePoint(r.geom,c.geom) AS fraction FROM corridor_choke_points c, route r
    WHERE (:fixtures OR c.source_scope='live')
      AND ST_DWithin(c.geom::geography,r.geom::geography,
          GREATEST(:radius,COALESCE(c.choke_radius_meters,500)))
    ORDER BY fraction,c.choke_id''')


async def corridor_topology(session, redis, route, settings, include_fixtures=False, radius=500):
    encoded = json.dumps(route.geojson, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(f'v1:{include_fixtures}:{radius}:{encoded}'.encode()).hexdigest()
    key = f'ts:scout:topology:{digest}'
    try:
        cached = await redis.get(key) if redis else None
        if cached:
            rows = json.loads(cached)
            if isinstance(rows, list) and all(isinstance(r, dict) and {'choke_id', 'longitude', 'latitude',
                'base_delay_minutes', 'peak_multiplier', 'fraction', 'choke_name'} <= r.keys() for r in rows):
                return rows, True
    except (RedisError, OSError, ValueError, TypeError):
        pass
    rows = [dict(row) for row in (await session.execute(TOPOLOGY,
        {'route': encoded, 'fixtures': include_fixtures, 'radius': radius})).mappings().all()]
    rows = json.loads(json.dumps(rows, default=str))
    try:
        if redis:
            await redis.set(key, json.dumps(rows), ex=settings.scout_topology_cache_ttl_seconds)
    except (RedisError, OSError):
        pass
    return rows, False


def nearby_hazards(row, hazards):
    point = [row['longitude'], row['latitude']]
    return [hazard for hazard in hazards if distance(point, [hazard.golden_record['location']['longitude'],
        hazard.golden_record['location']['latitude']]) <= (row.get('choke_radius_meters') or 500)]


def rank_chokes(rows, hazards, limit=5):
    if not 3 <= limit <= MAX_SCOUT_CHOKE_POINTS:
        raise ValueError('Scout choke cap must be 3..5')
    def risk(row):
        return float(row.get('base_delay_minutes') or 0)*float(row.get('peak_multiplier') or 1) + sum(
            hazard.delay_minutes for hazard in nearby_hazards(row, hazards))
    ranked = sorted(rows, key=lambda row: (-risk(row), row['fraction'], str(row['choke_id'])))[:limit]
    return sorted([{**row, 'risk_score': risk(row)} for row in ranked], key=lambda row: row['fraction'])


def sample_chokes(rows, hazards, route):
    # Fresh local Golden Records are telemetry; no unconfigured external traffic API is claimed.
    if len(rows) > MAX_SCOUT_CHOKE_POINTS:
        raise ValueError('Telemetry sampling exceeds cap')
    result = []
    for row in rows:
        nearby = nearby_hazards(row, hazards)
        offset, _ = route.project([row['longitude'], row['latitude']])
        result.append(RouteChokePoint(choke_id=row['choke_id'], choke_name=row['choke_name'],
            location=GeocodedLocation(longitude=row['longitude'], latitude=row['latitude'],
                                     method='provided', confidence=1),
            static_topology_score=row.get('static_topology_score') or 0,
            arrival_minutes=route.travel_minutes(offset), risk_score=row['risk_score'],
            delay_minutes=float(row.get('base_delay_minutes') or 0)*float(row.get('peak_multiplier') or 1),
            predicted_clearance_minutes=max((h.delay_minutes for h in nearby), default=None),
            overlaps_incident=any(h.overlap != 'CLEARED_BEFORE_ETA' for h in nearby)))
    return result


async def sample_traffic_chokes(rows, hazards, route, redis, settings, include_fixtures=False, client=None):
    local = sample_chokes(rows, hazards, route)
    return await sample_live_traffic(local, redis, settings, include_fixtures, client)
