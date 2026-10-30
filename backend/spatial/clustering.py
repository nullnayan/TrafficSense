# @file: backend/spatial/clustering.py
# @exports: cluster_report
# @depends_on: backend/spatial/{cluster_query,synthesis}.py
# @purpose: Incremental metric DBSCAN with <=60-minute total span and stable Golden Record merging.
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text

from backend.core.config import get_settings
from backend.spatial.cluster_query import component_rows
from backend.spatial.synthesis import synthesize, upsert_golden


async def cluster_report(session, report_id, attributes, settings=None):
    settings = settings or get_settings()
    # All spatial writers take this transaction lock before row locks to avoid deadlocks.
    await session.execute(text('SELECT pg_advisory_xact_lock(707202604)'))
    seed = (await session.execute(text('''
        SELECT *, ST_X(geom) AS longitude,ST_Y(geom) AS latitude
        FROM raw_reports WHERE report_id=:id
    '''), {'id': report_id})).mappings().one()
    window = timedelta(minutes=settings.dbscan_window_minutes)
    rows = await component_rows(session, report_id, attributes, seed,
        seed['published_at']-window, seed['published_at']+window, settings.dbscan_eps_meters)
    if not rows:
        raise ValueError('No active compatible geometry for clustering')
    anchor = min(row['published_at'] for row in rows)
    rows = await component_rows(session, report_id, attributes, seed, anchor, anchor+window,
                                settings.dbscan_eps_meters)
    if not rows:
        raise ValueError('No temporally bounded spatial component')
    existing = sorted({row['cluster_id'] for row in rows if row['cluster_id']}, key=str)
    cluster_id = existing[0] if existing else uuid4()
    superseded = existing[1:]
    canonical, score = synthesize(rows)
    revision = await upsert_golden(session, cluster_id, rows, canonical, score)
    ids = [row['report_id'] for row in rows]
    await session.execute(text('''
        UPDATE raw_reports SET cluster_id=:cluster,pipeline_status='CLUSTERED'
        WHERE report_id=ANY(CAST(:ids AS uuid[]))
    '''), {'cluster': cluster_id, 'ids': ids})
    if superseded:
        await session.execute(text('''
            UPDATE incident_clusters SET is_active=false,last_updated_at=now()
            WHERE cluster_id=ANY(CAST(:ids AS uuid[]))
        '''), {'ids': superseded})
    return {'cluster_id': cluster_id, 'cluster_revision': revision, 'report_count': len(ids),
            'superseded_cluster_ids': [str(value) for value in superseded]}
