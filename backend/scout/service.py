# @file: backend/scout/service.py
# @exports: ScoutService
# @depends_on: backend/scout/{osrm,topology,evaluator}.py
# @purpose: Compose route geometry, cached ranking, capped telemetry and transparent arrival verdicts.
from datetime import timedelta

from backend.schemas.spatial import RouteScoutResponse
from backend.scout.detour import blockade_detour
from backend.scout.evaluator import evaluate_overlap, route_incidents
from backend.scout.journey import plan_journey
from backend.scout.osrm import route_geometry
from backend.scout.topology import corridor_topology, rank_chokes, sample_traffic_chokes
from backend.scout.weather import fallback_weather


class ScoutService:
    def __init__(self, session_factory, redis, settings):
        self.session_factory, self.redis, self.settings = session_factory, redis, settings

    async def evaluate(self, request):
        async with self.session_factory() as session:
            route = await route_geometry(session, request, self.settings)
            records = await route_incidents(session, route, request.include_fixtures)
            hazards = sorted([evaluate_overlap(record, route, request.departure_at) for record in records],
                             key=lambda hazard: hazard.distance_along_route_meters)
            topology, hit = await corridor_topology(session, self.redis, route, self.settings,
                                                   request.include_fixtures, request.corridor_buffer_meters)
        selected = rank_chokes(topology, hazards, request.max_choke_points)
        chokes = await sample_traffic_chokes(selected, hazards, route, self.redis, self.settings,
                                            request.include_fixtures)
        chokes = await fallback_weather(chokes, self.settings, request.include_fixtures)
        hazards, chokes, journey = plan_journey(records, route, chokes, request.departure_at)
        async with self.session_factory() as session:
            detour = await blockade_detour(session, route, hazards, self.settings, request.include_fixtures)
        delay = sum(h.delay_minutes for h in hazards)+sum(c.delay_minutes for c in chokes)
        risk = ('HIGH' if any(h.overlap == 'BLOCKED_AT_ETA' for h in hazards) else
                'CAUTION' if any(h.overlap == 'RESIDUAL_RISK' for h in hazards) or chokes else 'LOW')
        recommendation = {'HIGH': 'Delay departure or consider an alternate route; blockage overlaps arrival.',
                          'CAUTION': 'Allow extra time for choke delays and possible residual queues.',
                          'LOW': 'No modeled active blockage at arrival; monitor for new reports.'}[risk]
        if route.source != 'osrm_live':
            recommendation += ' Fallback corridor geometry is approximate; verify roads before driving.'
        baseline = request.departure_at+timedelta(seconds=route.duration_seconds)
        return RouteScoutResponse(distance_meters=route.distance_meters, duration_seconds=route.duration_seconds,
            route_geojson=route.geojson, route_source=route.source, baseline_eta=baseline,
            adjusted_eta=baseline+timedelta(minutes=delay), adjusted_duration_seconds=route.duration_seconds+delay*60,
            choke_points=chokes, incident_cluster_ids=[h.cluster_id for h in hazards], incidents=hazards,
            risk_level=risk, recommendation=recommendation, advisory=recommendation, topology_cache_hit=hit,
            **journey, **detour)
