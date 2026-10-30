# @file: tests/test_ingestion_delivery.py
# @exports: Opt-in ingestion persistence, catch-up, replay, and failure recovery tests
# @depends_on: backend/ingestion/{catchup,macro_sentry,storage}.py, tests/conftest.py
# @purpose: Prove URL idempotency, heartbeat progress, and no lost/duplicated stream delivery.
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from backend.core.config import get_settings
from backend.core.redis_bus import StreamNames
from backend.ingestion.catchup import inspect_downtime_gap, run_catchup_backfill
from backend.ingestion.connectors import GDELTDocConnector, GoogleNewsRSSConnector
from backend.ingestion.macro_sentry import execute_macro_sentry_cycle
from backend.ingestion.storage import flush_outbox
from backend.schemas.ingestion import utc_now


async def seed_stale(factory):
    now = utc_now()
    async with factory() as session, session.begin():
        await session.execute(text('''
            INSERT INTO system_heartbeat (service_name, last_heartbeat_utc, status)
            VALUES ('national_macro_sentry', :last, 'initialized')
        '''), {'last': now - timedelta(hours=6)})
    return now


@pytest.mark.asyncio
async def test_catchup_persists_once_and_advances_heartbeat(ingestion_services):
    factory, bus = ingestion_services
    now = await seed_stale(factory)
    connectors = [GoogleNewsRSSConnector(mode='fixtures'), GDELTDocConnector(mode='fixtures')]
    with patch('backend.ingestion.catchup.utc_now', return_value=now):
        async with factory() as session:
            first = await run_catchup_backfill(session, bus, connectors)
        async with factory() as session:
            second = await execute_macro_sentry_cycle(session, bus, connectors)
    assert first['backfill'] and first['gap_hours'] == 6
    assert (first['fetched_count'], first['inserted_count'], first['published_count'],
            first['dropped_noise_count']) == (3, 3, 2, 1)
    assert second['inserted_count'] == second['published_count'] == 0
    assert await bus.client.xlen(StreamNames.RAW_ITEMS) == 2
    async with factory() as session:
        assert (await session.execute(text('SELECT count(*) FROM raw_reports'))).scalar_one() == 3
        heartbeat = (await session.execute(text('SELECT * FROM system_heartbeat'))).mappings().one()
    assert heartbeat['last_heartbeat_utc'] == now
    assert heartbeat['last_backfill_from_utc'] == now - timedelta(hours=6)


@pytest.mark.asyncio
async def test_redis_failure_recovers_without_reinsertion(ingestion_services):
    factory, bus = ingestion_services
    now = await seed_stale(factory)
    connectors = [GDELTDocConnector(mode='fixtures')]
    with patch('backend.ingestion.catchup.utc_now', return_value=now):
        with patch.object(bus, 'publish_once', new_callable=AsyncMock, side_effect=ConnectionError):
            async with factory() as session:
                failed = await run_catchup_backfill(session, bus, connectors)
        assert failed['pending_count'] == 1 and failed['status'] == 'degraded'
        async with factory() as session:
            recovered = await run_catchup_backfill(session, bus, connectors)
    assert recovered['inserted_count'] == 0 and recovered['published_count'] == 1
    assert recovered['pending_count'] == 0
    assert await bus.client.xlen(StreamNames.RAW_ITEMS) == 1


@pytest.mark.asyncio
async def test_redis_success_db_rollback_replays_without_duplicate(ingestion_services):
    factory, bus = ingestion_services
    now = await seed_stale(factory)
    with patch('backend.ingestion.catchup.utc_now', return_value=now):
        with patch.object(bus, 'publish_once', new_callable=AsyncMock, side_effect=ConnectionError):
            async with factory() as session:
                await run_catchup_backfill(session, bus, [GDELTDocConnector(mode='fixtures')])
    async with factory() as session:
        with patch.object(session, 'execute', wraps=session.execute) as execute:
            async def crash_after_publish(statement, *args, **kwargs):
                if 'UPDATE ingestion_outbox' in str(statement):
                    raise RuntimeError('Simulated crash before DB delivery acknowledgement')
                return await original(statement, *args, **kwargs)
            original = execute._mock_wraps
            execute.side_effect = crash_after_publish
            with pytest.raises(RuntimeError):
                await flush_outbox(session, bus)
    assert await bus.client.xlen(StreamNames.RAW_ITEMS) == 1
    async with factory() as session:
        published, delivered = await flush_outbox(session, bus)
    assert (published, delivered) == (0, 1)
    assert await bus.client.xlen(StreamNames.RAW_ITEMS) == 1


@pytest.mark.asyncio
async def test_failed_source_preserves_backfill_watermark(ingestion_services):
    factory, bus = ingestion_services
    now = await seed_stale(factory)
    source = GDELTDocConnector(mode='fixtures')
    with patch.object(source, 'fetch', new_callable=AsyncMock, side_effect=TimeoutError), \
         patch('backend.ingestion.catchup.utc_now', return_value=now):
        async with factory() as session:
            result = await run_catchup_backfill(session, bus, [source])
    assert result['status'] == 'degraded'
    async with factory() as session:
        stamp = (await session.execute(text('SELECT last_heartbeat_utc FROM system_heartbeat'))).scalar_one()
    assert stamp == now - timedelta(hours=6)


@pytest.mark.asyncio
async def test_live_mode_revisits_fixture_heartbeat(ingestion_services):
    factory, bus = ingestion_services
    now = await seed_stale(factory)
    live = get_settings().model_copy(update={'ingestion_source_mode': 'live'})
    with patch('backend.ingestion.catchup.utc_now', return_value=now), \
         patch('backend.ingestion.catchup.get_settings', return_value=live):
        async with factory() as session:
            await run_catchup_backfill(session, bus, [GDELTDocConnector(mode='fixtures')])
        async with factory() as session:
            _, _, gap = await inspect_downtime_gap(session)
    # Synthetic history must not count as live source coverage, regardless of local demo mode.
    assert gap == 48
