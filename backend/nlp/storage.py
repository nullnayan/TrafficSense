# @file: backend/nlp/storage.py
# @exports: prepare_stage, deliver_stage
# @depends_on: backend/nlp/{gatekeeper_model,extractor}.py, sql/04_nlp_states.sql
# @purpose: Row-locked NLP transitions and transactional outboxes before idempotent stream delivery.
import json

from sqlalchemy import text

from backend.core.redis_bus import StreamNames
from backend.schemas.enums import TemporalStatus
from backend.schemas.extraction import ExtractedIncidentSchema
from backend.schemas.ingestion import RawIngestedItem


async def prepare_stage(session, report_id, stage, classifier, extractor):
    metrics = {'processed_raw': 0, 'dropped_noise_or_inactive': 0, 'extracted_count': 0}
    async with session.begin():
        row = (await session.execute(text('SELECT * FROM raw_reports WHERE report_id=:id FOR UPDATE'),
                                     {'id': report_id})).mappings().one_or_none()
        if row is None:
            raise ValueError('Stream references a missing raw_reports row')
        item = RawIngestedItem.model_validate({name: row[name] for name in RawIngestedItem.model_fields})
        payload = None
        if stage == 'filter' and row['pipeline_status'] == 'raw':
            result = await classifier.classify(item)
            active = result.is_relevant and result.temporal_status == TemporalStatus.ACTIVE
            state = 'FILTERED_ACTIVE' if active else (
                'DROPPED_NOT_ACTIVE' if result.is_relevant else 'DROPPED_IRRELEVANT')
            await session.execute(text('''
                UPDATE raw_reports SET setfit_is_relevant=:relevant, setfit_confidence=:confidence,
                    temporal_status=:temporal, pipeline_status=:state WHERE report_id=:id
            '''), {'id': report_id, 'relevant': result.is_relevant, 'confidence': result.confidence,
                   'temporal': str(result.temporal_status), 'state': state})
            metrics.update(processed_raw=1, dropped_noise_or_inactive=int(not active))
            payload = item if active else None
        elif stage == 'extract' and row['pipeline_status'] in ('FILTERED_ACTIVE', 'filtered'):
            result = ExtractedIncidentSchema.model_validate((await extractor.extract(item)).model_dump())
            if result.report_id != report_id or result.temporal_status != TemporalStatus.ACTIVE:
                raise ValueError('Extraction identity or active temporal status mismatch')
            await session.execute(text('''
                UPDATE raw_reports SET landmark_tuple=CAST(:landmark AS jsonb),
                    extracted_attributes=CAST(:attributes AS jsonb), pipeline_status='EXTRACTED'
                WHERE report_id=:id
            '''), {'id': report_id, 'landmark': result.landmark_tuple.model_dump_json(),
                   'attributes': result.model_dump_json()})
            metrics['extracted_count'] = 1
            payload = result
        elif stage == 'extract' and row['pipeline_status'] == 'raw':
            raise ValueError('Filtered stream cannot bypass relevance classification')
        if payload is not None:
            await session.execute(text('''
                INSERT INTO nlp_stage_outbox (report_id, stage, payload)
                VALUES (:id, :stage, CAST(:payload AS jsonb)) ON CONFLICT DO NOTHING
            '''), {'id': report_id, 'stage': stage, 'payload': payload.model_dump_json()})
    return metrics


async def deliver_stage(session, bus, report_id, stage):
    async with session.begin():
        row = (await session.execute(text('''
            SELECT payload, delivered_at FROM nlp_stage_outbox
            WHERE report_id=:id AND stage=:stage FOR UPDATE
        '''), {'id': report_id, 'stage': stage})).mappings().one_or_none()
        if row is None or row['delivered_at'] is not None:
            return False
        model = RawIngestedItem if stage == 'filter' else ExtractedIncidentSchema
        values = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
        payload = model.model_validate(values)
        stream = StreamNames.FILTERED_ITEMS if stage == 'filter' else StreamNames.EXTRACTED_ITEMS
        _, is_new = await bus.publish_once(stream, payload, str(report_id))
        await session.execute(text('''
            UPDATE nlp_stage_outbox SET delivered_at=now() WHERE report_id=:id AND stage=:stage
        '''), {'id': report_id, 'stage': stage})
    return is_new
