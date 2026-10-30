# @file: backend/spatial/cluster_query.py
# @exports: component_rows
# @depends_on: sql/05_spatial_pipeline.sql
# @purpose: Deterministic metric DBSCAN components with whole-cluster temporal compatibility.
from sqlalchemy import text

COMPONENT = text('''
    WITH eligible AS (
        SELECT r.*, c.first_reported_at AS cluster_first, c.last_reported_at AS cluster_last,
            ST_Transform(r.geom, CAST(:projection AS text)) AS metric_geom
        FROM raw_reports r LEFT JOIN incident_clusters c ON c.cluster_id=r.cluster_id
        WHERE r.geom IS NOT NULL AND r.pipeline_status IN ('GEOCODED','CLUSTERED')
            AND r.temporal_status='active'
            AND r.extracted_attributes->>'incident_type'=:kind
            AND (r.source_name LIKE 'fixture:%')=:fixture
            AND r.published_at BETWEEN :since AND :until
            AND (r.cluster_id IS NULL OR (c.is_active AND c.first_reported_at >= :since
                AND COALESCE(c.last_reported_at,c.first_reported_at) <= :until))
    ), components AS (
        SELECT *, ST_ClusterDBSCAN(metric_geom, eps := :eps, minpoints := 1)
            OVER (ORDER BY published_at, report_id) AS component_id FROM eligible
    ) SELECT * FROM components WHERE component_id=(
        SELECT component_id FROM components WHERE report_id=:id
    ) ORDER BY published_at, report_id
''')


async def component_rows(session, report_id, attributes, seed, since, until, eps):
    # A local azimuthal equidistant projection avoids Web Mercator's latitude scale error.
    projection = f'+proj=aeqd +lat_0={seed["latitude"]:.8f} +lon_0={seed["longitude"]:.8f} +datum=WGS84 +units=m +no_defs'
    return (await session.execute(COMPONENT, {'id': report_id, 'kind': str(attributes.incident_type),
        'fixture': seed['source_name'].startswith('fixture:'), 'projection': projection,
        'since': since, 'until': until, 'eps': eps})).mappings().all()
