# @file: tests/test_phase7_sse_and_eval.py
# @exports: SSE, pipeline and summary regression/integration tests
# @depends_on: backend/streaming, backend/evaluation, tests/phase6_support.py
# @purpose: Prove non-consuming fan-out, replay, bounded frames and durable full-pipeline idempotency.
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fakeredis.aioredis import FakeRedis
from sqlalchemy import text

from backend.analytics.worker import AnalyticsService
from backend.core.config import get_settings
from backend.core.redis_bus import RedisStreamBus, StreamNames
from backend.evaluation.summary import summarize
from backend.spatial.records import list_active
from backend.spatial.worker import SpatialService
from backend.streaming.demo_pipeline import run_demo
from backend.streaming.sse import sse_frames
from tests.phase6_support import golden, phase6_db  # noqa: F401 - pytest fixture


def request(client, factory=None, resume=''):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        bus=RedisStreamBus(client), session_factory=factory,
        settings=SimpleNamespace(sse_heartbeat_seconds=.01))),
        headers={'last-event-id': resume}, is_disconnected=AsyncMock(return_value=False))


@pytest.mark.asyncio
async def test_live_fanout_reconnect_and_no_consumer_groups():
    async with FakeRedis(decode_responses=True) as client:
        record = golden(scope='fixture').model_dump(mode='json')
        envelope = {'golden_record': record, 'superseded_cluster_ids': [str(uuid4())]}
        first = sse_frames(request(client), include_fixtures=True, max_events=3)
        second = sse_frames(request(client), include_fixtures=True, max_events=3)
        assert 'event: ready' in await anext(first)
        assert 'event: ready' in await anext(second)
        # Publication after ready but before replay completion must not be lost.
        await RedisStreamBus(client).publish(StreamNames.GEOCODED_ITEMS, envelope)
        await anext(first)
        await anext(second)
        frames = await asyncio.gather(anext(first), anext(second))
        assert frames[0] == frames[1] and 'event: incident_active' in frames[0]
        assert envelope['superseded_cluster_ids'][0] in frames[0]
        cursor = frames[0].splitlines()[0].removeprefix('id: ')
        await first.aclose()
        await second.aclose()
        await RedisStreamBus(client).publish(StreamNames.ANALYZED_ITEMS, envelope)
        resumed = sse_frames(request(client, resume=cursor), include_fixtures=True, max_events=3)
        frames = [f async for f in resumed]
        assert 'event: incident_analyzed' in frames[-1]
        assert await client.xinfo_groups(StreamNames.GEOCODED_ITEMS) == []


@pytest.mark.asyncio
async def test_fixture_filter_poison_and_bounded_heartbeat():
    async with FakeRedis(decode_responses=True) as client:
        frames = sse_frames(request(client), max_events=3)
        await anext(frames)
        await anext(frames)
        await client.xadd(StreamNames.GEOCODED_ITEMS, {'payload': '{broken'})
        await RedisStreamBus(client).publish(StreamNames.GEOCODED_ITEMS,
            {'golden_record': golden(scope='fixture').model_dump(mode='json')})
        result = [f async for f in frames]
        assert len(result) == 1 and 'event: heartbeat' in result[0]


@pytest.mark.asyncio
async def test_active_snapshot_replay():
    async with FakeRedis(decode_responses=True) as client:
        def factory():
            return AsyncMock()
        record = golden()
        with patch('backend.streaming.sse.list_active', AsyncMock(return_value=[record])) as active:
            frames = [f async for f in sse_frames(request(client, factory), replay_active=True, max_events=3)]
        assert '"replaying": true' in frames[0] and 'event: incident_active' in frames[1]
        assert str(record.cluster_id) in frames[1] and '"replaying": false' in frames[2]
        assert active.await_args.args[2] is False


@pytest.mark.asyncio
async def test_real_redis_xread_fanout():
    if os.getenv('TRAFFICSENSE_INTEGRATION') != '1':
        pytest.skip('Opt in to local Redis integration')
    bus = RedisStreamBus.from_url(get_settings().redis_url.get_secret_value())
    names = (f'ts:test:phase7:{uuid4()}:geocoded', f'ts:test:phase7:{uuid4()}:analyzed')
    try:
        with patch('backend.streaming.sse.STREAMS', names):
            stream = sse_frames(request(bus.client), include_fixtures=True, max_events=3)
            await anext(stream)
            await anext(stream)
            await bus.client.xadd(names[1], {'payload': json.dumps({
                'golden_record': golden(scope='fixture').model_dump(mode='json')})})
            assert 'event: incident_analyzed' in await anext(stream)
            await stream.aclose()
            assert await bus.client.xinfo_groups(names[1]) == []
    finally:
        await bus.client.delete(*names)
        await bus.close()


@pytest.mark.asyncio
async def test_demo_all_stages_idempotent(phase6_db, tmp_path):  # noqa: F811 - imported pytest fixture
    fixture = phase6_db
    bus = RedisStreamBus(fixture.redis)
    state = SimpleNamespace(session_factory=fixture.factory, bus=bus, settings=fixture.settings,
        spatial=SpatialService(fixture.factory, bus, fixture.settings),
        analytics=AnalyticsService(fixture.factory, bus, fixture.settings))
    output = tmp_path / 'demo.json'
    first = await run_demo(state, output)
    assert first['stages']['ingest']['telemetry']['inserted'] == 3
    assert first['stages']['nlp']['telemetry']['extracted_count'] == 3
    assert first['stages']['spatial']['telemetry']['geocoded_count'] == 3
    assert first['stages']['analytics']['telemetry']['clusters_analyzed'] >= 1
    async with fixture.factory() as session:
        active = await list_active(session, include_fixtures=True)
        count = await session.scalar(text('SELECT count(*) FROM raw_reports'))
    assert any(r.source_scope == 'fixture' and r.clearance is not None for r in active)
    second = await run_demo(state, output)
    assert second['stages']['ingest']['telemetry']['inserted'] == 0
    assert second['stages']['nlp']['telemetry']['extracted_count'] == 0
    assert second['stages']['analytics']['telemetry']['published_count'] == 0
    async with fixture.factory() as session:
        assert await session.scalar(text('SELECT count(*) FROM raw_reports')) == count
    assert first['end_to_end_latency_ms'] >= sum(s['latency_ms'] for s in first['stages'].values()) - .01
    assert json.loads(output.read_text())['provenance'].startswith('synthetic_fixture')


def test_summary_generation_honest_missing_and_recorded(tmp_path):
    reports = tmp_path / 'training/reports'
    reports.mkdir(parents=True)
    (reports / 'qlora_training_metrics.json').write_text('{"train_loss": 0.9643}')
    result = summarize(tmp_path)
    path = tmp_path / 'benchmarks/thesis_evaluation_summary.json'
    assert path.is_file() and json.loads(path.read_text()) == result
    assert result['field_validation_status'] == 'not_performed'
    assert result['evidence']['qlora']['metrics']['train_loss'] == .9643
    assert result['evidence']['pipeline']['status'] == 'missing_not_measured'
    assert all(e['field_validated'] is False for e in result['evidence'].values())
