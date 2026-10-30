# @file: backend/ingestion/storage.py
# @exports: persist_items, flush_outbox, write_heartbeat
# @depends_on: backend/core/redis_bus.py, sql/03_ingestion_outbox.sql
# @purpose: Transactional URL deduplication, durable outbox, and monotonic ingestion watermark.
import json

from sqlalchemy import text

from backend.core.redis_bus import StreamNames
from backend.ingestion.deduplication import deduplicate, fingerprint
from backend.ingestion.gatekeeper import passes_macro_gatekeeper
from backend.schemas.ingestion import RawIngestedItem

SERVICE = 'national_macro_sentry'


async def persist_items(session, items):
    inserted, dropped = 0, 0
    async with session.begin():
        for item in deduplicate(items):
            allowed, reason = passes_macro_gatekeeper(item)
            values = item.model_dump(mode='python')
            values['source_url'] = str(item.source_url)
            values['content_fingerprint'] = fingerprint(item)
            values['pipeline_status'] = 'raw' if allowed else 'rejected'
            report_id = (await session.execute(text('''
                INSERT INTO raw_reports (report_id, source_name, source_type, source_url, title, raw_text,
                    published_at, ingested_at, pipeline_status, content_fingerprint)
                VALUES (:report_id, :source_name, :source_type, :source_url, :title, :raw_text,
                    :published_at, :ingested_at, :pipeline_status, :content_fingerprint)
                ON CONFLICT DO NOTHING RETURNING report_id
            '''), values)).scalar_one_or_none()
            if report_id is None:
                continue
            inserted += 1
            dropped += int(not allowed)
            if allowed:
                await session.execute(text('''
                    INSERT INTO ingestion_outbox (report_id, payload)
                    VALUES (:report_id, CAST(:payload AS jsonb)) ON CONFLICT DO NOTHING
                '''), {'report_id': report_id, 'payload': item.model_dump_json()})
    return inserted, dropped


async def flush_outbox(session, bus):
    published, delivered = 0, 0
    async with session.begin():
        rows = (await session.execute(text('''
            SELECT report_id, payload FROM ingestion_outbox WHERE delivered_at IS NULL
            ORDER BY created_at LIMIT 1000 FOR UPDATE SKIP LOCKED
        '''))).mappings().all()
        for row in rows:
            payload = row['payload']
            item = (RawIngestedItem.model_validate_json(payload) if isinstance(payload, str)
                    else RawIngestedItem.model_validate(payload))
            _, is_new = await bus.publish_once(StreamNames.RAW_ITEMS, item, str(row['report_id']))
            published += int(is_new)
            delivered += 1
            await session.execute(text('''
                UPDATE ingestion_outbox SET delivered_at=now() WHERE report_id=:id
            '''), {'id': row['report_id']})
    return published, delivered


async def write_heartbeat(session, since, until, summary, backfill=False):
    reliable = not summary['source_errors'] and not summary['fallback_sources'] and not summary['pending_count']
    async with session.begin():
        await session.execute(text('''
            INSERT INTO system_heartbeat (service_name, last_heartbeat_utc, status, details)
            VALUES (:service, :initial, :status, CAST(:details AS jsonb))
            ON CONFLICT (service_name) DO UPDATE SET
                last_heartbeat_utc=CASE WHEN :reliable THEN
                    GREATEST(system_heartbeat.last_heartbeat_utc, :until) ELSE
                    system_heartbeat.last_heartbeat_utc END,
                last_backfill_from_utc=CASE WHEN :backfill AND :reliable THEN :since ELSE
                    system_heartbeat.last_backfill_from_utc END,
                status=:status, details=CAST(:details AS jsonb)
        '''), {'service': SERVICE, 'initial': until if reliable else since, 'until': until,
               'since': since, 'status': 'healthy' if reliable else 'degraded',
               'reliable': reliable, 'backfill': backfill, 'details': json.dumps(summary)})
