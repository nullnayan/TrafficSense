# @file: backend/scout/geometry.py
# @exports: RouteGeometry, distance, segmented, fallback_geometry
# @depends_on: sqlalchemy, backend/schemas/spatial.py
# @purpose: Geodesic segmentation, cumulative distance/arrival and deterministic corridor waypoints.
import json
import math
from dataclasses import dataclass, field

from sqlalchemy import text

EARTH_RADIUS = 6371008.8


def distance(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [*a, *b])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2*EARTH_RADIUS*math.asin(math.sqrt(min(1, max(0, h))))


def segmented(points):
    result = [list(points[0])]
    for a, b in zip(points, points[1:]):
        # Spherical interpolation, including short routes, always supplies multiple segments.
        n = min(256, max(8, math.ceil(distance(a, b)/5000)))
        def vector(point):
            lon, lat = map(math.radians, point)
            return [math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat)]
        av, bv = vector(a), vector(b)
        angle = math.acos(min(1, max(-1, sum(x*y for x, y in zip(av, bv)))))
        if angle > math.pi-1e-6:
            raise ValueError('Antipodal endpoints require intermediate corridor landmarks')
        for i in range(1, n+1):
            f = i/n
            weights = ([1-f, f] if angle < 1e-8 else
                       [math.sin((1-f)*angle)/math.sin(angle), math.sin(f*angle)/math.sin(angle)])
            x, y, z = [weights[0]*u + weights[1]*v for u, v in zip(av, bv)]
            result.append([math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))])
    result[-1] = list(points[-1])
    return result


@dataclass
class RouteGeometry:
    coordinates: list
    duration_seconds: float
    source: str
    cumulative_meters: list = field(init=False)

    def __post_init__(self):
        if not 2 <= len(self.coordinates) <= 20000 or not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError('Route requires bounded geometry and positive finite duration')
        for point in self.coordinates:
            if len(point) != 2 or not all(math.isfinite(v) for v in point) or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90:
                raise ValueError('Invalid route coordinates')
        self.cumulative_meters = [0.0]
        for a, b in zip(self.coordinates, self.coordinates[1:]):
            self.cumulative_meters.append(self.cumulative_meters[-1]+distance(a, b))
        if self.distance_meters <= 0:
            raise ValueError('Origin and destination must differ')

    @property
    def distance_meters(self):
        return self.cumulative_meters[-1]

    @property
    def geojson(self):
        return {'type': 'LineString', 'coordinates': self.coordinates}

    def project(self, point):
        # Project each short segment in a local metric tangent plane, then use geodesic lengths.
        best = (float('inf'), 0.0)
        scale = math.cos(math.radians(point[1]))
        def xy(value):
            lon = (value[0]-point[0]+180) % 360-180
            return math.radians(lon)*EARTH_RADIUS*scale, math.radians(value[1]-point[1])*EARTH_RADIUS
        for i, (a, b) in enumerate(zip(self.coordinates, self.coordinates[1:])):
            ax, ay = xy(a)
            bx, by = xy(b)
            dx, dy = bx-ax, by-ay
            f = min(1, max(0, -(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx*dx+dy*dy else 0
            offset = self.cumulative_meters[i]+f*(self.cumulative_meters[i+1]-self.cumulative_meters[i])
            candidate = (math.hypot(ax+f*dx, ay+f*dy), offset)
            best = min(best, candidate)
        return best[1], best[0]

    def travel_minutes(self, offset):
        return self.duration_seconds/60*min(1, max(0, offset/self.distance_meters))


WAYPOINTS = text('''WITH route AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:route),4326) AS geom)
    SELECT ST_X(c.geom) AS longitude, ST_Y(c.geom) AS latitude,
        ST_LineLocatePoint(r.geom,c.geom) AS fraction FROM corridor_choke_points c, route r
    WHERE (:fixtures OR c.source_scope='live')
      AND (CAST(:corridor AS text) IS NULL OR regexp_replace(lower(c.corridor_name),'[^a-z0-9]','','g')=CAST(:corridor AS text))
      AND ST_DWithin(c.geom::geography,r.geom::geography,:radius)
      AND ST_LineLocatePoint(r.geom,c.geom) BETWEEN 0.01 AND 0.99
    ORDER BY fraction,c.choke_id LIMIT 24''')


async def fallback_geometry(session, origin, destination, settings, include_fixtures=False, corridor=None):
    import re
    a, b = [origin.longitude, origin.latitude], [destination.longitude, destination.latitude]
    direct = segmented([a, b])
    rows = (await session.execute(WAYPOINTS, {
        'route': json.dumps({'type': 'LineString', 'coordinates': direct}),
        'fixtures': include_fixtures, 'corridor': re.sub('[^a-z0-9]', '', corridor.lower()) if corridor else None,
        'radius': min(5000, max(500, distance(a, b)*0.15)),
    })).mappings().all()
    points = [a, *[[row['longitude'], row['latitude']] for row in rows], b]
    coordinates = segmented(points)
    meters = sum(distance(x, y) for x, y in zip(coordinates, coordinates[1:]))
    return RouteGeometry(coordinates, meters/(settings.scout_fallback_speed_kmh/3.6), 'corridor_waypoint_fallback')
