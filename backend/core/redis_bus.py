# @file: backend/core/redis_bus.py
# @exports: RedisStreamBus, StreamNames
# @depends_on: backend/core/config.py
# @purpose: Async Streams delivery, pending recovery, ACK, and atomic dead-letter routing.
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import ResponseError


class StreamNames(StrEnum):
    RAW_ITEMS = 'ts:stream:raw_items'
    FILTERED_ITEMS = 'ts:stream:filtered_items'
    EXTRACTED_ITEMS = 'ts:stream:extracted_items'
    GEOCODED_ITEMS = 'ts:stream:geocoded_items'
    ANALYZED_ITEMS = 'ts:stream:analyzed_items'
    DEAD_LETTER = 'ts:stream:dead_letter'


# Lua serializes the copy and ACK, and makes repeated routing a no-op after the ACK.
DEAD_LETTER_LUA = '''
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 then return false end
local id = redis.call('XADD', KEYS[2], '*', 'payload', ARGV[3])
redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
return id
'''

PUBLISH_ONCE_LUA = '''
local prior = redis.call('HGET', KEYS[2], ARGV[1])
if prior then return {prior, 0} end
local id = redis.call('XADD', KEYS[1], '*', 'payload', ARGV[2])
redis.call('HSET', KEYS[2], ARGV[1], id)
return {id, 1}
'''


class RedisStreamBus:
    def __init__(self, client: Redis):
        self.client = client

    @classmethod
    def from_url(cls, url: str) -> 'RedisStreamBus':
        return cls(Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=3,
            socket_timeout=10, health_check_interval=30,
        ))

    async def close(self) -> None:
        await self.client.aclose()

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def ensure_group(self, stream: StreamNames, group: str, start_id: str = '0-0') -> None:
        try:
            await self.client.xgroup_create(stream, group, id=start_id, mkstream=True)
        except ResponseError as exc:
            if not str(exc).startswith('BUSYGROUP'):
                raise

    async def publish(self, stream: StreamNames, payload: BaseModel | dict[str, Any]) -> str:
        encoded = payload.model_dump_json() if isinstance(payload, BaseModel) else json.dumps(
            payload, allow_nan=False,
        )
        # Do not trim here: trimming can delete unacknowledged messages.
        return await self.client.xadd(stream, {'payload': encoded})

    async def publish_once(self, stream: StreamNames, payload: BaseModel, key: str) -> tuple[str, bool]:
        result = await self.client.eval(
            PUBLISH_ONCE_LUA, 2, stream, f'{stream}:published_ids', key, payload.model_dump_json(),
        )
        return result[0], bool(result[1])

    async def read_batch(
        self, stream: StreamNames, group: str, consumer: str,
        count: int = 50, block_ms: int = 1000, pending: bool = False,
    ) -> list[tuple[str, dict[str, str]]]:
        if count < 1 or not 1 <= block_ms <= 5000:
            raise ValueError('count must be positive; block_ms must be 1..5000')
        result = await self.client.xreadgroup(
            group, consumer, {stream: '0-0' if pending else '>'},
            count=count, block=None if pending else block_ms,
        )
        return [(message_id, fields) for _, entries in result for message_id, fields in entries]

    async def reclaim_pending(
        self, stream: StreamNames, group: str, consumer: str,
        min_idle_ms: int = 60000, start_id: str = '0-0', count: int = 50,
    ) -> tuple[str, list]:
        if min_idle_ms < 0 or count < 1:
            raise ValueError('min_idle_ms must be nonnegative and count positive')
        result = await self.client.xautoclaim(
            stream, group, consumer, min_idle_ms, start_id=start_id, count=count,
        )
        return result[0], result[1]

    async def ack(self, stream: StreamNames, group: str, *message_ids: str) -> int:
        return int(await self.client.xack(stream, group, *message_ids)) if message_ids else 0

    async def dead_letter(
        self, stream: StreamNames, group: str, message_id: str, error: str,
    ) -> str | None:
        if stream == StreamNames.DEAD_LETTER:
            raise ValueError('Dead-letter stream cannot route to itself')
        entries = await self.client.xrange(stream, min=message_id, max=message_id, count=1)
        if not entries:
            raise ValueError('Original stream entry is missing; refusing to acknowledge it')
        envelope = json.dumps({
            'source_stream': str(stream), 'source_group': group, 'source_id': message_id,
            'original_fields': entries[0][1], 'error': error[:2000],
            'failed_at': datetime.now(timezone.utc).isoformat(),
        }, allow_nan=False)
        result = await self.client.eval(
            DEAD_LETTER_LUA, 2, stream, StreamNames.DEAD_LETTER, group, message_id, envelope,
        )
        return result or None
