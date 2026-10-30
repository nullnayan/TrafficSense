# @file: backend/scout/evaluator.py
# @exports: route_incidents, evaluate_overlap
# @depends_on: backend/analytics/waterfall.py, backend/spatial/records.py, backend/scout/geometry.py
# @purpose: Metric route hazard lookup and driver-arrival versus expected/p90 clearance overlap.
import json
from datetime import timedelta

from sqlalchemy import text

from backend.analytics.waterfall import estimate_clearance
from backend.schemas.route_hazard import RouteIncident
from backend.spatial.records import SELECT_RECORD, golden_from_row

INCIDENTS = text(SELECT_RECORD+''',
    LATERAL (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:route),4326) AS route_geom) r
    WHERE c.is_active AND cardinality(c.source_report_ids)>0
      AND (:fixtures OR c.source_scope='live')
      AND (ST_DWithin(c.centroid_geom::geography,r.route_geom::geography,500)
           OR ST_DWithin(c.impact_polygon_geom::geography,r.route_geom::geography,500))
    ORDER BY c.cluster_id''')


async def route_incidents(session, route, include_fixtures=False):
    rows = (await session.execute(INCIDENTS, {'route': json.dumps(route.geojson),
                                            'fixtures': include_fixtures})).mappings().all()
    return [golden_from_row(row) for row in rows]


def evaluate_overlap(record, route, departure_at):
    anchor = record.last_reported_at or record.last_updated_at
    breakdown = record.waterfall_breakdown or record.clearance
    if record.expected_clearance_minutes is None or breakdown is None:
        breakdown = estimate_clearance(record)
        expected = breakdown.estimated_clearance_mins
        record = record.model_copy(update={'expected_clearance_minutes': expected,
            'waterfall_breakdown': breakdown, 'clearance': breakdown,
            'clearance_upper_bound_minutes': breakdown.bayesian_bound_mins,
            'estimated_clear_at': anchor+timedelta(minutes=expected)})
    expected_at = record.estimated_clear_at or anchor+timedelta(minutes=record.expected_clearance_minutes)
    upper_minutes = record.clearance_upper_bound_minutes
    if upper_minutes is None:
        upper_minutes = breakdown.upper_bound_minutes or breakdown.bayesian_bound_mins
    upper_at = max(expected_at, anchor+timedelta(minutes=upper_minutes))
    offset, _ = route.project([record.location.longitude, record.location.latitude])
    travel = route.travel_minutes(offset)
    eta = departure_at+timedelta(minutes=travel)
    if eta <= expected_at:
        overlap, delay = 'BLOCKED_AT_ETA', max(0, (expected_at-eta).total_seconds()/60)
    elif eta <= upper_at:
        overlap, delay = 'RESIDUAL_RISK', max(0, (upper_at-eta).total_seconds()/60)*0.25
    else:
        overlap, delay = 'CLEARED_BEFORE_ETA', 0
    return RouteIncident(cluster_id=record.cluster_id, overlap=overlap, arrival_at=eta,
        travel_minutes_to_incident=travel, distance_along_route_meters=offset,
        estimated_clear_at=expected_at, clear_upper_at=upper_at, delay_minutes=delay,
        golden_record=record.model_dump(mode='json'))
