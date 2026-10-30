# @file: backend/assistant/sql_templates.py
# @exports: TEMPLATES, bound_parameters, execute_template
# @depends_on: sqlalchemy, backend/schemas/assistant.py
# @purpose: Immutable read-only query registry with explicit typed binds, scope and row caps.
from types import MappingProxyType

from sqlalchemy import text

from backend.schemas.assistant import SlotFillingQuery
from backend.schemas.enums import QueryIntent

PARAMETERS = '''WITH p AS (SELECT CAST(:incident_type AS text) AS kind,
    CAST(:severity AS text) AS severity, CAST(:city AS text) AS city,
    CAST(:corridor AS text) AS corridor, CAST(:latitude AS float8) AS latitude,
    CAST(:longitude AS float8) AS longitude, CAST(:radius AS float8) AS radius,
    COALESCE(CAST(:start_at AS timestamptz),CAST(:as_of AS timestamptz)
        -CAST(:time_window_hours AS float8)*interval '1 hour') AS since,
    COALESCE(CAST(:end_at AS timestamptz),CAST(:as_of AS timestamptz)) AS until,
    CAST(:include_fixtures AS boolean) AS fixtures)
'''
ACTIVE_FILTER = '''c.is_active AND cardinality(c.source_report_ids)>0
    AND (p.fixtures OR c.source_scope='live')
    AND (p.kind IS NULL OR c.incident_type=p.kind)
    AND (p.severity IS NULL OR c.severity=p.severity)
    AND (p.city IS NULL OR lower(c.city)=lower(p.city))
    AND (p.corridor IS NULL OR regexp_replace(lower(c.corridor_name),'[^a-z0-9]','','g')
        =regexp_replace(lower(p.corridor),'[^a-z0-9]','','g'))
    AND COALESCE(c.last_reported_at,c.last_updated_at) BETWEEN p.since AND p.until'''
ACTIVE_SELECT = '''SELECT c.cluster_id,c.incident_type,c.severity,c.corridor_name,c.city,
    c.expected_clearance_minutes,c.estimated_clear_at,c.last_reported_at,
    ST_X(c.centroid_geom) AS longitude,ST_Y(c.centroid_geom) AS latitude,c.source_scope
    FROM incident_clusters c CROSS JOIN p WHERE '''+ACTIVE_FILTER
HISTORY_FILTER = '''(p.fixtures OR h.source_scope='live')
    AND (p.kind IS NULL OR h.incident_type=p.kind)
    AND (p.severity IS NULL OR h.severity=p.severity)
    AND (p.corridor IS NULL OR regexp_replace(lower(h.corridor_name),'[^a-z0-9]','','g')
        =regexp_replace(lower(p.corridor),'[^a-z0-9]','','g'))
    AND h.recorded_at BETWEEN p.since AND p.until
    AND (p.city IS NULL OR EXISTS (SELECT 1 FROM gazetteer_places g
        WHERE lower(g.city_name)=lower(p.city)
        AND ST_DWithin(g.geom::geography,h.geom::geography,15000)))'''

TEMPLATES = MappingProxyType({
    QueryIntent.NEARBY_INCIDENTS: text(PARAMETERS+ACTIVE_SELECT+'''
        AND (ST_DWithin(c.centroid_geom::geography,
            ST_SetSRID(ST_MakePoint(p.longitude,p.latitude),4326)::geography,p.radius)
          OR ST_DWithin(c.impact_polygon_geom::geography,
            ST_SetSRID(ST_MakePoint(p.longitude,p.latitude),4326)::geography,p.radius))
        ORDER BY c.last_updated_at DESC,c.cluster_id LIMIT :limit'''),
    QueryIntent.CONGESTION_HOTSPOTS: text(PARAMETERS+ACTIVE_SELECT+'''
        ORDER BY c.expected_clearance_minutes DESC NULLS LAST,c.report_count DESC,c.cluster_id LIMIT :limit'''),
    QueryIntent.CORRIDOR_HISTORY: text(PARAMETERS+'''SELECT h.history_id,h.corridor_name,h.incident_type,
        h.severity,h.observed_clearance_mins,h.recorded_at,h.source_scope
        FROM corridor_history h CROSS JOIN p WHERE '''+HISTORY_FILTER+'''
        ORDER BY h.recorded_at DESC,h.history_id LIMIT :limit'''),
    QueryIntent.AVERAGE_CLEARANCE: text(PARAMETERS+''', samples AS (
        SELECT c.corridor_name,c.expected_clearance_minutes AS minutes,c.source_scope
        FROM incident_clusters c CROSS JOIN p WHERE '''+ACTIVE_FILTER+'''
        AND c.expected_clearance_minutes IS NOT NULL
        UNION ALL SELECT h.corridor_name,h.observed_clearance_mins,h.source_scope
        FROM corridor_history h CROSS JOIN p WHERE '''+HISTORY_FILTER+''')
        SELECT corridor_name,avg(minutes) AS average_clearance_minutes,count(*) AS sample_count,
            bool_or(source_scope='fixture') AS includes_fixtures FROM samples
        GROUP BY corridor_name ORDER BY average_clearance_minutes DESC,corridor_name LIMIT :limit'''),
})


def bound_parameters(query, include_fixtures, as_of):
    slots = query.slots
    return {'incident_type': slots.incident_type.value if slots.incident_type else None,
        'severity': slots.severity.value if slots.severity else None, 'city': slots.city,
        'corridor': slots.corridor_name, 'time_window_hours': slots.time_window_hours,
        'latitude': slots.latitude, 'longitude': slots.longitude, 'radius': slots.radius_meters,
        'start_at': slots.start_at, 'end_at': slots.end_at, 'as_of': as_of,
        'include_fixtures': include_fixtures, 'limit': slots.limit}


async def execute_template(session, query, include_fixtures, as_of):
    # Revalidation also protects internal callers and objects created with model_construct.
    query = SlotFillingQuery.model_validate(query.model_dump())
    template = TEMPLATES[query.sql_template_id]
    async with session.begin():
        await session.execute(text('SET TRANSACTION READ ONLY'))
        await session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        result = await session.execute(template, bound_parameters(query, include_fixtures, as_of))
        return [dict(row) for row in result.mappings().all()][:query.slots.limit]
