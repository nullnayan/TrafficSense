# @file: backend/ingestion/transport.py
# @exports: fetch_payload, weather_context
# @depends_on: backend/ingestion/fixtures.py
# @purpose: Bounded HTTP retries and optional timestamped weather observations.
import asyncio

import httpx


async def fetch_payload(client: httpx.AsyncClient, url: str, params: dict, attempts: int = 3):
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params, timeout=12)
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise ValueError('Feed exceeds 5 MB parsing limit')
            content = response.text.lstrip()
            return response.json() if content.startswith(('{', '[')) else content
        except httpx.HTTPError as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in (
                408, 429, 500, 502, 503, 504,
            )
            if attempt + 1 == attempts or not retryable:
                raise
            await asyncio.sleep(0.25 * 2 ** attempt)


async def weather_context(client, row: dict, city_defaults: dict) -> dict | None:
    latitude, longitude = row.get('latitude'), row.get('longitude')
    if latitude is None or longitude is None:
        coordinates = city_defaults.get(str(row.get('city', '')).casefold())
        if not coordinates:
            return None
        latitude, longitude = coordinates
    try:
        latitude, longitude = float(latitude), float(longitude)
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            return None
        payload = await fetch_payload(client, 'https://api.open-meteo.com/v1/forecast', {
            'latitude': latitude, 'longitude': longitude,
            'current': 'precipitation,weather_code', 'timezone': 'UTC',
        }, attempts=1)
        current = payload['current']
        return {'provider': 'open-meteo', 'observed_at': current['time'],
                'context_only': True, 'precipitation_mm': current['precipitation'],
                'weather_code': current['weather_code']}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
