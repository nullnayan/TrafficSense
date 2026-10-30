# @file: backend/ingestion/connectors.py
# @exports: GoogleNewsRSSConnector, GDELTDocConnector, CivicAndPoliceAdvisoryConnector, build_connectors
# @depends_on: backend/ingestion/{parsing,transport,fixtures}.py
# @purpose: Windowed Indian incident feeds with explicit live/fixture/fallback modes.
import json
from datetime import datetime, timedelta

import httpx

from backend.ingestion.fixtures import fixture_payload
from backend.ingestion.parsing import parse_records, validate_window
from backend.ingestion.providers import omnichannel_connectors
from backend.ingestion.transport import fetch_payload, weather_context


class FeedConnector:
    name = ''
    url = ''

    def __init__(self, client=None, mode='live'):
        if mode not in ('live', 'fixtures', 'fallback'):
            raise ValueError('Unknown source mode')
        self.client, self.mode = client, mode
        self.used_fixture, self.invalid_count, self.truncated = False, 0, False
        self.last_error = None

    def parameters(self, since, until):
        return {}

    async def fetch(self, since_utc: datetime, until_utc: datetime) -> list:
        since, until = validate_window(since_utc, until_utc)
        self.used_fixture, self.invalid_count, self.truncated, self.last_error = False, 0, False, None
        owned = self.client is None
        client = self.client or httpx.AsyncClient(headers={'User-Agent': 'TrafficSense-GEU-MP2026CSE126'})
        try:
            try:
                if self.mode == 'fixtures':
                    self.used_fixture = True
                    payload = fixture_payload(self.name, until)
                else:
                    payload = await fetch_payload(client, self.url, self.parameters(since, until))
                source = f'fixture:{self.name}' if self.used_fixture else self.name
                records, self.invalid_count = parse_records(payload, source, since, until)
                self.truncated = isinstance(payload, dict) and len(payload.get('articles', [])) >= 250
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                self.last_error = type(exc).__name__
                if self.mode != 'fallback':
                    raise
                self.used_fixture = True
                records, self.invalid_count = parse_records(
                    fixture_payload(self.name, until), f'fixture:{self.name}', since, until,
                )
            return await self.enrich(client, records)
        finally:
            if owned:
                await client.aclose()

    async def enrich(self, client, records):
        return [item for item, _ in records]


class GoogleNewsRSSConnector(FeedConnector):
    name = 'google_news_rss'
    url = 'https://news.google.com/rss/search'

    def parameters(self, since, until):
        terms = '(NH-7 OR Dehradun OR India) (landslide OR waterlogging OR "highway blocked" OR "fatal crash")'
        return {'q': f'{terms} after:{since:%Y-%m-%d} before:{(until + timedelta(days=1)):%Y-%m-%d}',
                'hl': 'en-IN', 'gl': 'IN', 'ceid': 'IN:en'}


class GDELTDocConnector(FeedConnector):
    name = 'gdelt_doc'
    url = 'https://api.gdeltproject.org/api/v2/doc/doc'

    def parameters(self, since, until):
        return {'query': '(India OR Dehradun) (highway OR road) (landslide OR flood OR blockade OR crash)',
                'mode': 'artlist', 'format': 'json', 'maxrecords': 250, 'sort': 'datedesc',
                'startdatetime': since.strftime('%Y%m%d%H%M%S'),
                'enddatetime': until.strftime('%Y%m%d%H%M%S')}


class CivicAndPoliceAdvisoryConnector(FeedConnector):
    name = 'civic_police_advisory'

    def __init__(self, url='', client=None, mode='live', weather_enabled=False, city_defaults=None):
        super().__init__(client, mode)
        self.url, self.weather_enabled = url, weather_enabled
        self.city_defaults = city_defaults or {'dehradun': (30.3256, 78.0418)}

    def parameters(self, since, until):
        return {'since': since.isoformat(), 'until': until.isoformat()}

    async def enrich(self, client, records):
        results = []
        cache = {}
        for item, row in records:
            if self.weather_enabled and not self.used_fixture:
                key = (row.get('latitude'), row.get('longitude'), row.get('city'))
                if key not in cache:
                    cache[key] = await weather_context(client, row, self.city_defaults)
                if cache[key]:
                    suffix = '\n[weather_context ' + json.dumps(cache[key]) + ']'
                    item.raw_text = item.raw_text[:100000-len(suffix)] + suffix
            results.append(item)
        return results


def build_connectors(settings):
    mode = settings.ingestion_source_mode
    connectors = [GoogleNewsRSSConnector(mode=mode), GDELTDocConnector(mode=mode)]
    urls = settings.advisory_feed_urls
    if not urls and mode != 'live':
        urls = ['']
    connectors.extend(CivicAndPoliceAdvisoryConnector(
        url=url, mode=mode, weather_enabled=settings.ingestion_weather_enabled,
    ) for url in urls)
    return connectors + omnichannel_connectors(settings)
