# @file: tests/test_nlp_pipeline.py
# @exports: Opt-in end-to-end filtering/extraction, replay, outage, and poison-message tests
# @depends_on: backend/nlp/pipeline.py, tests/nlp_support.py
# @purpose: Verify actual Postgres transitions and Redis handoffs without inference services.
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from nlp_support import insert_report
from nlp_support import nlp_services as _nlp_services
from sqlalchemy import text

from backend.core.redis_bus import StreamNames
from backend.nlp.pipeline import process_filtered_stream_batch, process_raw_stream_batch
from backend.schemas.ingestion import RawIngestedItem

nlp_services = _nlp_services


def report(title):
    return RawIngestedItem(source_name='annotated_pipeline_test', source_url=f'https://example.org/{uuid4()}',
                           title=title, raw_text=title, published_at='2026-10-01T00:00:00Z')


@pytest.mark.asyncio
async def test_active_only_and_idempotent_extraction(nlp_services):
    factory, bus = nlp_services
    items = [report(title) for title in ['Landslide blocks NH-7 near Kali Mandir in Dehradun.',
        'Traffic restored on NH-7.', 'NH-44 will remain closed tomorrow.',
        'Cricket fever brings city to a standstill.']]
    for item in items:
        await insert_report(factory, item)
        await bus.publish(StreamNames.RAW_ITEMS, item)
    async with factory() as session:
        raw = await process_raw_stream_batch(session, bus)
    assert (raw['processed_raw'], raw['active_forwarded'], raw['dropped_noise_or_inactive']) == (4, 1, 3)
    async with factory() as session:
        extracted = await process_filtered_stream_batch(session, bus)
    assert extracted['extracted_count'] == 1
    assert await bus.client.xlen(StreamNames.EXTRACTED_ITEMS) == 1
    await bus.publish(StreamNames.RAW_ITEMS, items[0])
    await bus.publish(StreamNames.FILTERED_ITEMS, items[0])
    async with factory() as session:
        assert (await process_raw_stream_batch(session, bus))['active_forwarded'] == 0
        assert (await process_filtered_stream_batch(session, bus))['extracted_count'] == 0
    assert await bus.client.xlen(StreamNames.EXTRACTED_ITEMS) == 1
    async with factory() as session:
        rows = (await session.execute(text('SELECT pipeline_status, extracted_attributes FROM raw_reports'))).mappings().all()
    assert {row['pipeline_status'] for row in rows} == {'EXTRACTED', 'DROPPED_NOT_ACTIVE', 'DROPPED_IRRELEVANT'}
    attributes = next(row['extracted_attributes'] for row in rows if row['pipeline_status'] == 'EXTRACTED')
    assert attributes['lanes_blocked'] is attributes['total_lanes'] is None
    assert attributes['landmark_tuple']['primary_landmark'] == 'Kali Mandir'


@pytest.mark.asyncio
async def test_schema_poison_goes_to_dead_letter(nlp_services):
    factory, bus = nlp_services
    await bus.client.xadd(StreamNames.RAW_ITEMS, {'payload': '{invalid JSON'})
    async with factory() as session:
        stats = await process_raw_stream_batch(session, bus)
    assert stats['dead_letter_count'] == 1
    assert (await bus.client.xpending(StreamNames.RAW_ITEMS, 'nlp_filter_group'))['pending'] == 0
    assert await bus.client.xlen(StreamNames.DEAD_LETTER) == 1


@pytest.mark.asyncio
async def test_stream_failure_retains_pending_and_recovers_outbox(nlp_services):
    factory, bus = nlp_services
    item = report('Landslide blocks NH-7.')
    await insert_report(factory, item)
    await bus.publish(StreamNames.RAW_ITEMS, item)
    with patch.object(bus, 'publish_once', new_callable=AsyncMock, side_effect=ConnectionError):
        async with factory() as session:
            failed = await process_raw_stream_batch(session, bus)
    assert failed['deferred_count'] == 1
    assert (await bus.client.xpending(StreamNames.RAW_ITEMS, 'nlp_filter_group'))['pending'] == 1
    async with factory() as session:
        recovered = await process_raw_stream_batch(session, bus)
    assert recovered['active_forwarded'] == 1 and recovered['processed_raw'] == 0
    assert await bus.client.xlen(StreamNames.FILTERED_ITEMS) == 1


@pytest.mark.asyncio
async def test_extraction_validation_poison_is_not_published(nlp_services):
    factory, bus = nlp_services
    item = report('Crash blocks one lane on NH-44.')
    await insert_report(factory, item)
    await bus.publish(StreamNames.RAW_ITEMS, item)
    async with factory() as session:
        await process_raw_stream_batch(session, bus)
    extractor = AsyncMock()
    extractor.extract.side_effect = ValueError('Invalid structured JSON')
    async with factory() as session:
        stats = await process_filtered_stream_batch(session, bus, extractor=extractor)
    assert extractor.extract.await_count == 3
    assert stats['dead_letter_count'] == 1
    assert await bus.client.xlen(StreamNames.EXTRACTED_ITEMS) == 0


@pytest.mark.asyncio
async def test_publish_success_then_rollback_does_not_duplicate(nlp_services):
    factory, bus = nlp_services
    item = report('Active landslide blocks NH-7.')
    await insert_report(factory, item)
    await bus.publish(StreamNames.RAW_ITEMS, item)
    original = bus.publish_once
    async def crash_after_publish(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError('Simulated lost connection after Redis publish')
    with patch.object(bus, 'publish_once', side_effect=crash_after_publish):
        async with factory() as session:
            assert (await process_raw_stream_batch(session, bus))['deferred_count'] == 1
    async with factory() as session:
        replay = await process_raw_stream_batch(session, bus)
    assert replay['active_forwarded'] == 0
    assert await bus.client.xlen(StreamNames.FILTERED_ITEMS) == 1
    assert (await bus.client.xpending(StreamNames.RAW_ITEMS, 'nlp_filter_group'))['pending'] == 0
