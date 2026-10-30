# @file: tests/test_omnichannel_social_gov.py
# @exports: Public social and official hazard mock tests
# @depends_on: backend/ingestion/{social_omnichannel_client,x_twitter_client,gov_hazard_client}.py
# @purpose: Verify HTML/JSON/XML parsing, region restrictions and unconfirmed hazard provenance.
import json

import httpx
import pytest

from backend.core.config import Settings
from backend.ingestion.gov_hazard_client import GovHazardClient
from backend.ingestion.social_omnichannel_client import SocialClient
from backend.ingestion.x_twitter_client import XTwitterClient
from tests.test_omnichannel_news import NOW, SINCE, STAMP


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['telegram', 'reddit', 'bluesky'])
async def test_social_parsers(kind):
    if kind == 'telegram':
        payload = f'<div data-post="abpganga/12"><div class="tgme_widget_message_text">NH-7 <b>landslide</b><br>road blocked</div><time datetime="{STAMP}"></time></div>'
    elif kind == 'reddit':
        payload = {'data': {'children': [{'data': {'title': 'NH-7 road landslide', 'selftext': 'closed',
            'created_utc': NOW.timestamp()-100, 'permalink': '/r/uttarakhand/comments/a/road/'}}]}}
    else:
        payload = {'posts': [{'uri': 'at://did:plc:a/app.bsky.feed.post/123',
                              'record': {'text': 'NH7 road blocked', 'createdAt': STAMP}}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:
        httpx.Response(200, text=payload) if isinstance(payload, str) else httpx.Response(200, json=payload))) as client:
        result = await SocialClient(kind, Settings(), client, kind=kind, channel='abpganga', subreddit='india').fetch(SINCE, NOW)
    assert len(result) == 1 and 'NH' in result[0].title and result[0].published_at < NOW
    assert '[source_timestamp' not in result[0].raw_text


@pytest.mark.asyncio
@pytest.mark.parametrize('authenticated', [False, True])
async def test_x_nested_timeline_and_cookie_search(authenticated):
    data = {'props': {'pageProps': {'timeline': {'entries': [{'content': {'tweet': {
        'id_str': '123', 'full_text': 'NH-7 road blocked after landslide', 'created_at': STAMP}}}]}}}}
    calls = []
    settings = Settings(x_auth_token='test-cookie', x_ct0='test-csrf', x_bearer_token='test-bearer',
                        x_search_query_id='operation-id')
    def handler(request):
        calls.append(request)
        return (httpx.Response(200, json=data) if authenticated else
                httpx.Response(200, text='<script id="__NEXT_DATA__">'+json.dumps(data)+'</script>'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        options = {} if authenticated else {'handle': 'uttarakhandcops'}
        items = await XTwitterClient('x', settings, client, **options).fetch(SINCE, NOW)
    assert len(items) == 1 and str(items[0].source_url).endswith('/status/123')
    if authenticated:
        assert calls[0].headers['x-csrf-token'] == 'test-csrf'
        assert 'auth_token=test-cookie' in calls[0].headers['cookie']


@pytest.mark.asyncio
async def test_syndication_missing_json_reports_failure():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<html/>'))) as client:
        with pytest.raises(ValueError):
            await XTwitterClient('x', Settings(), client, handle='ndmaindia').fetch(SINCE, NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['sachet', 'eonet', 'usgs', 'gdacs', 'data_gov'])
async def test_government_hazard_parsers(kind):
    if kind == 'sachet':
        payload = f'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>a1</identifier><sent>{STAMP}</sent><info><event>Flood warning</event><headline>Uttarakhand flood warning</headline><description>IMD official weather context</description></info></alert>'
    elif kind == 'eonet':
        event = {'title': 'Uttarakhand landslide', 'link': 'https://eonet.gsfc.nasa.gov/api/v3/events/a',
                 'geometry': [{'type': 'Point', 'coordinates': [78, 30], 'date': STAMP}]}
        payload = {'events': [event, {**event, 'geometry': [{'type': 'Point', 'coordinates': [-120, 40], 'date': STAMP}]}]}
    elif kind == 'usgs':
        payload = {'features': [{'properties': {'title': 'Himalayan earthquake', 'url': 'https://earthquake.usgs.gov/a',
                    'time': (NOW.timestamp()-100)*1000}, 'geometry': {'type': 'Point', 'coordinates': [78, 30, 5]}}]}
    elif kind == 'gdacs':
        payload = f'<rss xmlns:gdacs="https://www.gdacs.org"><channel><item><title>India flood</title><link>https://gdacs.org/a</link><pubDate>{STAMP}</pubDate><gdacs:country>India</gdacs:country></item><item><title>Other flood</title><link>https://gdacs.org/b</link><pubDate>{STAMP}</pubDate><gdacs:country>USA</gdacs:country></item></channel></rss>'
    else:
        payload = {'records': [{'title': 'NH-7 road blocked', 'description': 'Official road advisory', 'published_at': STAMP},
                               {'population': 3000}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:
        httpx.Response(200, text=payload) if isinstance(payload, str) else httpx.Response(200, json=payload))) as client:
        items = await GovHazardClient(kind, Settings(data_gov_in_api_key='test'), client,
                                     kind=kind, resource='r1').fetch(SINCE, NOW)
    assert len(items) == 1
    if kind == 'usgs':
        assert 'unconfirmed' in items[0].raw_text
