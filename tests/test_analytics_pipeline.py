# @file: tests/test_analytics_pipeline.py
# @exports: Metric SQL, recovery, revision, supersession, lifecycle and poison regression tests
# @depends_on: backend/analytics, tests/analytics_support.py
# @purpose: Exercise real PostGIS transactions with Redis publication/ACK failure injection.
from datetime import timedelta

import pytest
from sqlalchemy import text

from backend.analytics.history import clearance_samples
from backend.analytics.lifecycle import expire_elapsed_clusters
from backend.analytics.pipeline import GROUP, process_geocoded_stream_batch
from backend.analytics.storage import prepare_analytics
from backend.core.redis_bus import StreamNames
from backend.spatial.records import list_active, read_golden
from backend.spatial.synthesis import upsert_golden
from tests.analytics_support import (
    NOW,
    add_choke,
    add_history,
    envelope,
    golden,
    insert_cluster,
)
from tests.analytics_support import (
    analytics_services as analytics_services,
)


@pytest.mark.asyncio
async def test_metric_scope_and_choke_transfer(analytics_services):
    factory, _ = analytics_services
    async with factory() as session, session.begin():
        for offset in (0, 1000, 1001, 15000, 15001):
            await add_history(session, offset=offset, road='Other Road')
        await add_history(session, offset=50000, road='nh 58')
        await add_history(session, kind='protest')
        await add_choke(session, offset=499)
        await add_choke(session, offset=501)
        empirical, neighbors = await clearance_samples(session, golden(), 40)
        assert len(empirical) == 3  # same normalized road or inclusive 1km
        assert len(neighbors) == 4  # two histories + two chokes; inclusive 15km
        chokes = sorted((row for row in neighbors if row['source'] == 'choke_prior'),
                        key=lambda row: row['distance'])
        assert [row['minutes'] for row in chokes] == [80, 60]
        assert chokes[0]['weight'] > chokes[1]['weight']
        assert await clearance_samples(session, golden(source_scope='live'), 40) == ([], [])
        live_empirical, _ = await clearance_samples(session, golden(source_scope='live'), 40, True)
        assert len(live_empirical) == 3 and all(row['source_scope'] == 'fixture' for row in live_empirical)


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_at', ['publish', 'delivery_commit', 'ack'])
async def test_outbox_recovers_before_ack_without_duplicate_publication(analytics_services, monkeypatch, fail_at):
    factory, bus = analytics_services
    record = await insert_cluster(factory)
    await bus.publish(StreamNames.GEOCODED_ITEMS, envelope(record))
    async with factory() as session:
        target, method = (session, 'execute') if fail_at == 'delivery_commit' else (
            bus, 'publish_once' if fail_at == 'publish' else 'ack')
        original = getattr(target, method)
        async def fail(*args, **kwargs):
            if fail_at != 'delivery_commit' or 'SET delivered_at' in str(args[0]):
                raise OSError('Injected outage')
            return await original(*args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(target, method, fail)
            with pytest.raises(OSError):
                await process_geocoded_stream_batch(session, bus, now=NOW)
        assert (await bus.client.xpending(StreamNames.GEOCODED_ITEMS, GROUP))['pending'] == 1
        result = await process_geocoded_stream_batch(session, bus, now=NOW)
        assert result['clusters_analyzed'] == 0  # persisted estimate survives transport failure
        assert await bus.client.xlen(StreamNames.ANALYZED_ITEMS) == 1
        assert (await bus.client.xpending(StreamNames.GEOCODED_ITEMS, GROUP))['pending'] == 0
        active = await read_golden(session, record.cluster_id)
        assert active.expected_clearance_minutes and active.waterfall_breakdown.selected_level == 3


@pytest.mark.asyncio
async def test_latest_revision_invalidates_expiry_and_replay_is_idempotent(analytics_services):
    factory, bus = analytics_services
    record = await insert_cluster(factory)
    await bus.publish(StreamNames.GEOCODED_ITEMS, envelope(record))
    async with factory() as session:
        assert (await process_geocoded_stream_batch(session, bus, now=NOW))['clusters_analyzed'] == 1
        async with session.begin():
            await session.execute(text('''UPDATE incident_clusters SET cluster_revision=2,
                last_reported_at=:at WHERE cluster_id=:id'''),
                {'at': NOW + timedelta(minutes=30), 'id': record.cluster_id})
            assert await expire_elapsed_clusters(session, NOW + timedelta(minutes=80)) == 0
        await bus.publish(StreamNames.GEOCODED_ITEMS, envelope(record))  # stale envelope analyzes DB revision 2
        assert (await process_geocoded_stream_batch(session, bus, now=NOW))['clusters_analyzed'] == 1
        await bus.publish(StreamNames.GEOCODED_ITEMS, envelope(record))
        assert (await process_geocoded_stream_batch(session, bus, now=NOW))['clusters_analyzed'] == 0
        assert await bus.client.xlen(StreamNames.ANALYZED_ITEMS) == 2
        async with session.begin():
            assert await expire_elapsed_clusters(session, NOW + timedelta(minutes=77, seconds=59)) == 0
            assert await expire_elapsed_clusters(session, NOW + timedelta(minutes=78)) == 1
            assert await expire_elapsed_clusters(session, NOW + timedelta(minutes=79)) == 0
        assert await list_active(session) == []


@pytest.mark.asyncio
async def test_superseded_late_envelope_and_obsolete_outbox(analytics_services):
    factory, bus = analytics_services
    survivor = await insert_cluster(factory)
    loser = await insert_cluster(factory)
    async with factory() as session:
        await prepare_analytics(session, envelope(loser))  # unpublished old outbox
        async with session.begin():
            await session.execute(text('UPDATE incident_clusters SET is_active=false WHERE cluster_id=:id'),
                                  {'id': loser.cluster_id})
            await session.execute(text('UPDATE raw_reports SET cluster_id=:winner WHERE report_id=:id'),
                                  {'winner': survivor.cluster_id, 'id': loser.report_id})
        await bus.publish(StreamNames.GEOCODED_ITEMS, envelope(loser))
        result = await process_geocoded_stream_batch(session, bus, now=NOW)
        assert result['clusters_analyzed'] == 1 and await bus.client.xlen(StreamNames.ANALYZED_ITEMS) == 1
        active = await list_active(session)
        assert len(active) == 1 and active[0].cluster_id == survivor.cluster_id


@pytest.mark.asyncio
async def test_abandoned_consumer_and_poison_recovery(analytics_services):
    factory, bus = analytics_services
    record = await insert_cluster(factory)
    await bus.publish(StreamNames.GEOCODED_ITEMS, envelope(record))
    await bus.ensure_group(StreamNames.GEOCODED_ITEMS, GROUP)
    entries = await bus.read_batch(StreamNames.GEOCODED_ITEMS, GROUP, 'abandoned', block_ms=1)
    await bus.client.xclaim(StreamNames.GEOCODED_ITEMS, GROUP, 'abandoned', 0,
                            [entries[0][0]], idle=61000)
    async with factory() as session:
        assert (await process_geocoded_stream_batch(session, bus, now=NOW))['clusters_analyzed'] == 1
        await bus.client.xadd(StreamNames.GEOCODED_ITEMS, {'payload': '{broken'})
        for _ in range(2):
            assert (await process_geocoded_stream_batch(session, bus, now=NOW))['dead_letter_count'] == 0
        assert (await process_geocoded_stream_batch(session, bus, now=NOW))['dead_letter_count'] == 1
        assert await bus.client.xlen(StreamNames.DEAD_LETTER) == 1
        assert (await bus.client.xpending(StreamNames.GEOCODED_ITEMS, GROUP))['pending'] == 0


@pytest.mark.asyncio
async def test_fresh_corroboration_reactivates_expired_cluster(analytics_services):
    factory, bus = analytics_services
    record = await insert_cluster(factory)
    async with factory() as session:
        await bus.publish(StreamNames.GEOCODED_ITEMS, envelope(record))
        assert (await process_geocoded_stream_batch(session, bus, now=NOW + timedelta(hours=2)))[
            'clusters_expired'] == 1
        async with session.begin():
            await session.execute(text('UPDATE raw_reports SET geom=ST_SetSRID(ST_MakePoint(77,30),4326) '
                                       'WHERE report_id=:id'), {'id': record.report_id})
            base = record.__class__.__bases__[0]
            attrs = {key: value for key, value in record.model_dump().items() if key in base.model_fields}
            revision = await upsert_golden(session, record.cluster_id, [{
                'report_id': record.report_id, 'published_at': NOW + timedelta(hours=3),
                'source_name': 'fixture:analytics', 'geocode_confidence': 1}], base.model_validate(attrs), 0.5)
            row = (await session.execute(text('SELECT * FROM incident_clusters WHERE cluster_id=:id'),
                                         {'id': record.cluster_id})).mappings().one()
            assert revision == 2 and row['is_active'] and row['expired_at'] is None
            assert row['estimated_clear_at'] is None and row['waterfall_breakdown'] is None
            assert await expire_elapsed_clusters(session, NOW + timedelta(hours=4)) == 0
