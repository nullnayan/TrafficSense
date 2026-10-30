# @file: backend/scout/weather.py
# @exports: fallback_weather
# @depends_on: backend/ingestion/transport.py
# @purpose: Capped opt-in precipitation context alongside local lane-reduction estimates after flow failure.
import asyncio

import httpx

from backend.ingestion.transport import weather_context


async def fallback_weather(chokes, settings, include_fixtures=False, client=None):
    if not settings.scout_live_traffic_enabled or include_fixtures or settings.ingestion_source_mode == 'fixtures':
        return chokes
    if client is None:
        async with httpx.AsyncClient(timeout=2.5) as owned:
            return await fallback_weather(chokes, settings, include_fixtures, owned)

    async def enrich(choke):
        if choke.telemetry_source != 'local_incident_clusters':
            return choke
        try:
            context = await asyncio.wait_for(weather_context(client, {
                'latitude': choke.location.latitude, 'longitude': choke.location.longitude}, {}), 2.5)
            return choke.model_copy(update={'weather_context': context}) if context else choke
        except TimeoutError:
            return choke

    return list(await asyncio.gather(*(enrich(choke) for choke in chokes[:5])))
