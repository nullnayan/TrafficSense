# @file: backend/analytics/lifecycle.py
# @exports: expire_elapsed_clusters
# @depends_on: sql/06_analytics_pipeline.sql
# @purpose: Deterministic expiry only for the analyzed latest corroborating revision.
from sqlalchemy import text


async def expire_elapsed_clusters(session, now):
    """Caller owns the transaction; estimate is anchored to the latest report (no grace)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Lifecycle sweep requires an aware timestamp')
    rows = (await session.execute(text('''
        UPDATE incident_clusters SET is_active=false, temporal_status='cleared', expired_at=:now
        WHERE is_active AND analyzed_revision=cluster_revision AND estimated_clear_at<=:now
            AND estimated_clear_at >= last_reported_at
        RETURNING cluster_id
    '''), {'now': now})).all()
    return len(rows)
