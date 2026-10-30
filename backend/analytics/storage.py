# @file: backend/analytics/storage.py
# @exports: AnalyzedEnvelope, prepare_analytics, deliver_analytics, flush_outbox
# @depends_on: backend/analytics/waterfall.py, backend/spatial/records.py, sql/06_analytics_pipeline.sql
# @purpose: Row-locked latest-revision estimates and durable publish-once delivery before ACK.
import json
from uuid import UUID

from pydantic import Field
from sqlalchemy import text

from backend.analytics.waterfall import analyze_golden
from backend.core.redis_bus import StreamNames
from backend.schemas.enums import ContractModel
from backend.schemas.spatial import GoldenRecordSchema
from backend.spatial.records import read_golden


class AnalyzedEnvelope(ContractModel):
    cluster_id: UUID
    cluster_revision: int = Field(ge=1)
    golden_record: GoldenRecordSchema


async def prepare_analytics(session, envelope, include_seed=False):
    async with session.begin():
        # The source report's current membership resolves delayed superseded envelopes.
        cluster_id = (await session.execute(text('''
            SELECT cluster_id FROM raw_reports WHERE report_id=:id
        '''), {'id': envelope.report_id})).scalar_one_or_none()
        if cluster_id is None:
            raise ValueError('Geocoded source report has no persisted cluster')
        row = (await session.execute(text('''
            SELECT cluster_revision,analyzed_revision,is_active FROM incident_clusters
            WHERE cluster_id=:id FOR UPDATE
        '''), {'id': cluster_id})).mappings().one()
        if not row['is_active']:
            return None, False
        revision = row['cluster_revision']
        key = (cluster_id, revision)
        if row['analyzed_revision'] >= revision:
            return key, False
        golden = await analyze_golden(session, await read_golden(session, cluster_id), include_seed)
        breakdown = golden.waterfall_breakdown
        params = {'id': cluster_id, 'revision': revision, 'expected': golden.expected_clearance_minutes,
                  'upper': breakdown.upper_bound_minutes, 'at': golden.estimated_clear_at,
                  'breakdown': breakdown.model_dump_json(), 'level': breakdown.selected_level,
                  'ceiling': breakdown.hard_ceiling_mins, 'queue': breakdown.pcu_queue_load,
                  'capacity': breakdown.discharge_capacity_pcu_hr}
        await session.execute(text('''
            UPDATE incident_clusters SET analyzed_revision=:revision,expected_clearance_minutes=:expected,
                clearance_upper_bound_minutes=:upper,estimated_clear_at=:at,predicted_clear_at=:at,
                waterfall_breakdown=CAST(:breakdown AS jsonb),estimated_clearance_mins=:expected,
                bayesian_bound_mins=:upper,hard_ceiling_mins=:ceiling,waterfall_level_used=:level,
                pcu_queue_load=:queue,discharge_capacity_pcu_hr=:capacity WHERE cluster_id=:id
        '''), params)
        payload = AnalyzedEnvelope(cluster_id=cluster_id, cluster_revision=revision, golden_record=golden)
        await session.execute(text('''
            INSERT INTO analytics_stage_outbox(cluster_id,cluster_revision,payload)
            VALUES (:id,:revision,CAST(:payload AS jsonb)) ON CONFLICT DO NOTHING
        '''), {**params, 'payload': payload.model_dump_json()})
        return key, breakdown


async def deliver_analytics(session, bus, key):
    async with session.begin():
        params = {'id': key[0], 'revision': key[1]}
        # Consistent cluster -> outbox lock order also protects publication from concurrent revisions.
        current = (await session.execute(text('''
            SELECT cluster_revision,is_active FROM incident_clusters WHERE cluster_id=:id FOR UPDATE
        '''), params)).mappings().one()
        row = (await session.execute(text('''
            SELECT * FROM analytics_stage_outbox WHERE cluster_id=:id AND cluster_revision=:revision
            FOR UPDATE
        '''), params)).mappings().one_or_none()
        if row is None:
            raise RuntimeError('Analyzed revision is missing its durable outbox')
        if row['delivered_at'] or row['skipped_at']:
            return False
        if not current['is_active'] or current['cluster_revision'] != key[1]:
            await session.execute(text('''UPDATE analytics_stage_outbox SET skipped_at=now()
                WHERE cluster_id=:id AND cluster_revision=:revision'''), params)
            return False
        values = row['payload']
        payload = AnalyzedEnvelope.model_validate(json.loads(values) if isinstance(values, str) else values)
        _, published = await bus.publish_once(StreamNames.ANALYZED_ITEMS, payload, f'{key[0]}:{key[1]}')
        await session.execute(text('''UPDATE analytics_stage_outbox SET delivered_at=now()
            WHERE cluster_id=:id AND cluster_revision=:revision'''), params)
        return published


async def flush_outbox(session, bus, limit=200):
    async with session.begin():
        keys = (await session.execute(text('''SELECT cluster_id,cluster_revision FROM analytics_stage_outbox
            WHERE delivered_at IS NULL AND skipped_at IS NULL ORDER BY created_at LIMIT :limit
        '''), {'limit': limit})).all()
    published = sum([int(await deliver_analytics(session, bus, tuple(key))) for key in keys])
    return published, len(keys)
