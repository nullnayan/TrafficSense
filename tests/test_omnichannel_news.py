# @file: tests/test_omnichannel_news.py
# @exports: Authenticated news, RSS and TomTom mock contract tests
# @depends_on: backend/ingestion/{news_api_client,rss_client,tomtom_client}.py
# @purpose: Verify all key adapters, timestamps, request filters and structured closure identity offline.
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.core.config import Settings
from backend.ingestion.news_api_client import NewsAPIClient
from backend.ingestion.rss_client import NewsRSSClient
from backend.ingestion.tomtom_client import TomTomIncidentClient

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
SINCE = NOW-timedelta(hours=6)
STAMP = (NOW-timedelta(minutes=20)).isoformat()
ARTICLE = {'title': 'NH-7 road blocked by landslide', 'url': 'https://news.example/1',
           'description': 'Road closed near Rishikesh', 'publishedAt': STAMP}
RSS = f'<rss><channel><item><title>NH-7 blocked</title><link>https://news.example/1</link><pubDate>{STAMP}</pubDate></item></channel></rss>'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,collection', [('serper', 'organic'), ('tavily', 'results'),
    ('newsdata', 'results'), ('currents', 'news'), ('gnews', 'articles'), ('youtube', 'items')])
async def test_each_authenticated_api(kind, collection):
    calls = []
    settings = Settings(**{kind+'_api_key': 'test-only'}, ingestion_source_mode='live')
    row = dict(ARTICLE)
    if kind == 'serper':
        row.pop('publishedAt')
        row['snippet'] = row['description']
    if kind == 'newsdata':
        row.pop('publishedAt')
        row['pubDate'] = '2026-10-14 11:40:00'
    if kind == 'youtube':
        row = {'id': {'videoId': 'v123'}, 'snippet': ARTICLE}
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={collection: [row]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await NewsAPIClient(kind, settings, client, kind=kind).fetch(SINCE, NOW)
    assert len(items) == 1 and items[0].source_type == kind
    assert SINCE <= items[0].published_at < NOW
    request = calls[0]
    assert request.extensions['timeout']['read'] == 6
    if kind == 'serper':
        assert b'"gl":"in"' in request.content and b'qdr:d' in request.content
        assert 'observed_at' in items[0].raw_text
    if kind == 'tavily':
        assert b'"days":1' in request.content and b'"topic":"news"' in request.content
    if kind == 'youtube':
        assert request.url.params['regionCode'] == 'IN' and str(items[0].source_url).endswith('v=v123')
    if kind == 'newsdata':
        assert request.url.params['language'] == 'en,hi'


@pytest.mark.asyncio
@pytest.mark.parametrize('engine,recency', [('google', '1h'), ('google', '6h'), ('bing', '1h')])
async def test_recency_rss(engine, recency):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=RSS)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await NewsRSSClient('rss', Settings(), client, engine=engine, recency=recency).fetch(SINCE, NOW)
    assert len(items) == 1
    assert calls[0].url.params['format'] == 'rss' if engine == 'bing' else 'when:'+recency in calls[0].url.params['q']


@pytest.mark.asyncio
async def test_tomtom_ongoing_incident_and_corridor_boxes():
    calls = []
    incident = {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [78.2, 30.2]},
                'properties': {'id': 'tt1', 'iconCategory': 8, 'from': 'NH-7 Rishikesh',
                               'startTime': '2026-10-01T12:00:00Z', 'events': [{'description': 'Landslide'}]}}
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'incidents': [incident]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await TomTomIncidentClient('tomtom_incidents', Settings(tomtom_api_key='test'), client).fetch(SINCE, NOW)
    assert len(items) == 1 and items[0].source_type == 'tomtom_incidents'
    assert 'road closed' in items[0].title and '2026-10-01' in items[0].raw_text
    assert len(calls) == 3 and len({r.url.params['bbox'] for r in calls}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [401, 429, 503])
async def test_http_errors_visible_without_fixture_substitution(code):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(code))) as client:
        source = NewsAPIClient('gnews', Settings(gnews_api_key='test'), client, kind='gnews')
        with pytest.raises(httpx.HTTPStatusError):
            await source.fetch(SINCE, NOW)
    assert source.last_error == 'HTTPStatusError' and not source.used_fixture
