# @file: backend/ingestion/source.py
# @exports: HTTPSource, observation, walk_dicts, disruption
# @depends_on: httpx, backend/ingestion/parsing.py
# @purpose: Shared bounded requests, isolated source errors and explicit observation-time provenance.
import asyncio
import re
from datetime import timedelta

import httpx

from backend.ingestion.parsing import parse_records, validate_window

DISRUPTION = re.compile(r'traffic|landslide|accident|blocked|closure|waterlogging|flood|roadwork|'
                        r'earthquake|cloudburst|railway|भूस्खलन|सड़क|यातायात|बाढ़', re.I)


def disruption(text):
    return bool(DISRUPTION.search(text or ''))


def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def observation(title, url, content, stamp, until):
    if not stamp:
        stamp = (until-timedelta(microseconds=1)).isoformat()
        content = str(content or title)+'\n[source_timestamp: observed_at; publication time unavailable]'
    return {'title': title, 'url': url, 'description': content or title, 'published_at': stamp}


class HTTPSource:
    mode = 'live'
    used_fixture = False
    truncated = False
    invalid_count = 0
    last_error = None

    def __init__(self, name, settings, client=None, **options):
        self.name, self.settings, self.client, self.options = name, settings, client, options

    async def request(self, client, url, method='GET', **kwargs):
        response = await client.request(method, url, timeout=6, **kwargs)
        response.raise_for_status()
        if len(response.content) > 5_000_000:
            raise ValueError('Response exceeds 5 MB')
        return response

    async def rows(self, client, since, until):
        raise NotImplementedError

    async def fetch(self, since, until):
        since, until = validate_window(since, until)
        self.last_error, self.invalid_count = None, 0
        owned = self.client is None
        client = self.client or httpx.AsyncClient(headers={'User-Agent': 'TrafficSense/0.8 public-feed-reader'})
        try:
            rows = await asyncio.wait_for(self.rows(client, since, until), 6)
            records, self.invalid_count = parse_records(rows, self.name, since, until)
            for item, _ in records:
                item.source_type = self.options.get('source_type', self.name)
            return [item for item, _ in records]
        except Exception as exc:
            self.last_error = type(exc).__name__
            raise
        finally:
            if owned:
                await client.aclose()
