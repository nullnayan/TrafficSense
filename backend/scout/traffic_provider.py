# @file: backend/scout/traffic_provider.py
# @exports: MAX_SCOUT_CHOKE_POINTS, sample_live_traffic
# @depends_on: httpx, Redis, backend/schemas/spatial.py
# @purpose: Opt-in capped TomTom/HERE speed samples with 2.5s deadlines and 60s credential-scoped cache.
import asyncio
import hashlib
import math
from typing import Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from backend.schemas.ingestion import utc_now

MAX_SCOUT_CHOKE_POINTS = 5
TIMEOUT_SECONDS = 2.5
CACHE_SECONDS = 60


class FlowSample(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    telemetry_source: Literal['tomtom_live', 'here_live']
    live_speed_ratio: float = Field(ge=0, le=1)
    current_speed_kmh: float = Field(ge=0)
    free_flow_speed_kmh: float = Field(gt=0)
    traffic_measured_at: AwareDatetime
    traffic_confidence: float | None = Field(default=None, ge=0, le=1)


async def fetch_flow(choke, provider, key, client):
    point = f'{choke.location.latitude},{choke.location.longitude}'
    if provider == 'tomtom':
        url = 'https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json'
        params = {'point': point, 'key': key, 'unit': 'KMPH'}
    else:
        url = 'https://data.traffic.hereapi.com/v7/flow'
        params = {'in': f'circle:{point};r=50', 'locationReferencing': 'none', 'apiKey': key}
    response = await asyncio.wait_for(client.get(url, params=params, timeout=TIMEOUT_SECONDS), TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    flow = data['flowSegmentData'] if provider == 'tomtom' else data['results'][0]['currentFlow']
    current = float(flow['currentSpeed'] if provider == 'tomtom' else flow['speed'])
    free = float(flow['freeFlowSpeed'] if provider == 'tomtom' else flow['freeFlow'])
    if not math.isfinite(current) or not math.isfinite(free) or current < 0 or free <= 0:
        raise ValueError('Invalid flow speed sample')
    multiplier = 1 if provider == 'tomtom' else 3.6  # HERE supplies metres/second.
    return FlowSample(telemetry_source=f'{provider}_live', live_speed_ratio=min(1, current / free),
                      current_speed_kmh=current * multiplier, free_flow_speed_kmh=free * multiplier,
                      traffic_measured_at=utc_now(), traffic_confidence=flow.get('confidence'))


async def cached_flow(choke, redis, provider, key, client):
    identity = f'{provider}:{key}:{choke.choke_id}:{choke.location.latitude}:{choke.location.longitude}'
    cache_key = f'ts:scout:flow:{hashlib.sha256(identity.encode()).hexdigest()}'
    try:
        encoded = await redis.get(cache_key) if redis else None
        if encoded:
            sample = FlowSample.model_validate_json(encoded)
            if sample.telemetry_source == f'{provider}_live':
                return choke.model_copy(update={**sample.model_dump(), 'traffic_cache_hit': True})
    except (RedisError, OSError, ValueError, TypeError):
        pass
    try:
        sample = await fetch_flow(choke, provider, key, client)
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, IndexError, TypeError, OSError):
        return choke
    try:
        if redis:
            await redis.set(cache_key, sample.model_dump_json(), ex=CACHE_SECONDS)
    except (RedisError, OSError):
        pass
    return choke.model_copy(update=sample.model_dump())


async def sample_live_traffic(chokes, redis, settings, include_fixtures=False, client=None):
    selected = chokes[:MAX_SCOUT_CHOKE_POINTS]
    if (not settings.scout_live_traffic_enabled or include_fixtures or
            settings.ingestion_source_mode == 'fixtures' or not selected):
        return selected
    tomtom = settings.tomtom_api_key.get_secret_value().strip()
    here = settings.here_api_key.get_secret_value().strip()
    provider, key = ('tomtom', tomtom) if tomtom else ('here', here)
    if not key:
        return selected
    if client is None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as owned:
            return await sample_live_traffic(selected, redis, settings, include_fixtures, owned)
    return await asyncio.gather(*(cached_flow(choke, redis, provider, key, client) for choke in selected))
