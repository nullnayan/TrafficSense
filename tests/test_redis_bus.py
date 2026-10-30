# @file: tests/test_redis_bus.py
# @exports: Redis Streams regression tests
# @depends_on: backend/core/redis_bus.py
# @purpose: Exercise group isolation, crash recovery, ACK, and loss-resistant dead letters.
import json
from unittest.mock import AsyncMock

import pytest
from fakeredis.aioredis import FakeRedis
from redis.exceptions import ResponseError

from backend.core.redis_bus import RedisStreamBus, StreamNames


@pytest.mark.asyncio
async def test_delivery_ack_and_group_isolation():
    async with FakeRedis(decode_responses=True) as client:
        bus = RedisStreamBus(client)
        stream = StreamNames.RAW_ITEMS
        item_id = await bus.publish(stream, {'text': 'One lane closed'})
        for group in ['filter', 'audit']:
            await bus.ensure_group(stream, group)
            await bus.ensure_group(stream, group)
            batch = await bus.read_batch(stream, group, 'worker', block_ms=1)
            assert batch[0][0] == item_id
            assert json.loads(batch[0][1]['payload'])['text'] == 'One lane closed'
            assert await bus.ack(stream, group, item_id) == 1
        assert await bus.ack(stream, 'filter') == 0


@pytest.mark.asyncio
async def test_recovery_and_idempotent_dead_letter():
    async with FakeRedis(decode_responses=True) as client:
        bus = RedisStreamBus(client)
        stream = StreamNames.EXTRACTED_ITEMS
        await bus.ensure_group(stream, 'geocoder')
        item_id = await bus.publish(stream, {'invalid': 'payload'})
        await bus.read_batch(stream, 'geocoder', 'crashed', block_ms=1)
        _, recovered = await bus.reclaim_pending(stream, 'geocoder', 'replacement', min_idle_ms=0)
        assert recovered[0][0] == item_id
        assert (await bus.read_batch(stream, 'geocoder', 'replacement', pending=True))[0][0] == item_id
        assert await bus.dead_letter(stream, 'geocoder', item_id, 'schema rejected')
        assert await bus.dead_letter(stream, 'geocoder', item_id, 'retry') is None
        assert (await client.xpending(stream, 'geocoder'))['pending'] == 0
        letters = await client.xrange(StreamNames.DEAD_LETTER)
        assert len(letters) == 1
        payload = json.loads(letters[0][1]['payload'])
        assert payload['source_id'] == item_id
        assert json.loads(payload['original_fields']['payload']) == {'invalid': 'payload'}


@pytest.mark.asyncio
async def test_failed_dead_letter_does_not_ack():
    async with FakeRedis(decode_responses=True) as client:
        bus = RedisStreamBus(client)
        stream = StreamNames.RAW_ITEMS
        await bus.ensure_group(stream, 'filter')
        item_id = await bus.publish(stream, {'text': 'test'})
        await bus.read_batch(stream, 'filter', 'worker', block_ms=1)
        await client.set(StreamNames.DEAD_LETTER, 'wrong-type')
        with pytest.raises(ResponseError):
            await bus.dead_letter(stream, 'filter', item_id, 'error')
        assert (await client.xpending(stream, 'filter'))['pending'] == 1


@pytest.mark.asyncio
async def test_non_busy_group_errors_propagate():
    client = AsyncMock()
    client.xgroup_create.side_effect = ResponseError('NOPERM denied')
    with pytest.raises(ResponseError, match='NOPERM'):
        await RedisStreamBus(client).ensure_group(StreamNames.RAW_ITEMS, 'filter')
