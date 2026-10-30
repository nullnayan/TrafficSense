# @file: tests/test_ingestion.py
# @exports: Gatekeeper, feed parsing, retry/fallback, and six-hour catch-up tests
# @depends_on: backend/ingestion
# @purpose: Deterministic offline coverage for Phase 2 public ingestion contracts.
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.ingestion.catchup import inspect_downtime_gap, run_catchup_backfill
from backend.ingestion.connectors import (
    CivicAndPoliceAdvisoryConnector,
    GDELTDocConnector,
    GoogleNewsRSSConnector,
)
from backend.ingestion.fixtures import fixture_payload
from backend.ingestion.gatekeeper import passes_macro_gatekeeper
from backend.ingestion.parsing import parse_records
from backend.schemas.ingestion import RawIngestedItem

NOW = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
SINCE = NOW - timedelta(hours=6)


@pytest.mark.parametrize('title,allowed', [
    ('Landslide blocks NH-7 near Dehradun', True), ('Severe waterlogging closes Delhi road', True),
    ('Fatal crash on Indian highway', True), ('Chakka jam brings road blockade', True),
    ('Bridge washed away; highway closed', True), ('NH-7 route diversion announced', True),
    ('Cricket team traffic standstill', False), ('Stock market crash after earnings', False),
    ('Film star causes traffic on social media', False), ('Normal road traffic at noon', False),
])
def test_gatekeeper(title, allowed):
    item = RawIngestedItem(source_name='test', source_url='https://example.org/report',
                           title=title, raw_text=title, published_at=NOW)
    result, reason = passes_macro_gatekeeper(item)
    assert result == allowed and reason


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,connector_type', [
    ('google_news_rss', GoogleNewsRSSConnector), ('gdelt_doc', GDELTDocConnector),
    ('civic_police_advisory', CivicAndPoliceAdvisoryConnector),
])
async def test_connector_parsing_with_mock_http(kind, connector_type):
    payload = fixture_payload(kind, NOW)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=payload) if isinstance(payload, str) else httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = (connector_type(url='https://mirror.example.org/feed', client=client)
                     if kind == 'civic_police_advisory' else connector_type(client=client))
        items = await connector.fetch(SINCE, NOW)
    assert items and all(isinstance(item, RawIngestedItem) for item in items)
    assert all(SINCE <= item.published_at < NOW for item in items)
    assert requests and connector.invalid_count == 0
    if kind == 'gdelt_doc':
        assert requests[0].url.params['startdatetime'] == '20261014063000'


@pytest.mark.asyncio
async def test_retry_and_explicit_fallback():
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch('backend.ingestion.transport.asyncio.sleep', new_callable=AsyncMock):
            connector = GoogleNewsRSSConnector(client=client, mode='fallback')
            assert (await connector.fetch(SINCE, NOW))[0].source_name.startswith('fixture:')
            assert connector.last_error == 'HTTPStatusError' and len(attempts) == 3
            with pytest.raises(httpx.HTTPStatusError):
                await GoogleNewsRSSConnector(client=client).fetch(SINCE, NOW)


@pytest.mark.asyncio
async def test_weather_context_is_optional_and_explicit():
    def handler(request):
        if request.url.host == 'api.open-meteo.com':
            return httpx.Response(200, json={'current': {'time': '2026-10-14T12:00',
                                                       'precipitation': 2.5, 'weather_code': 61}})
        return httpx.Response(200, json=fixture_payload('civic_police_advisory', NOW))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = CivicAndPoliceAdvisoryConnector('https://mirror.example.org', client,
                                                   weather_enabled=True)
        items = await connector.fetch(SINCE, NOW)
    assert '[weather_context' in items[0].raw_text and '"context_only": true' in items[0].raw_text


def test_invalid_dates_and_unrecognized_payloads():
    rows, invalid = parse_records({'articles': [{'url': 'https://example.org', 'title': 'NH-7 closed',
                                                'seendate': 'not-a-date'}]}, 'test', SINCE, NOW)
    assert rows == [] and invalid == 1
    with pytest.raises(ValueError):
        parse_records({'error': 'rate limited'}, 'test', SINCE, NOW)
    with pytest.raises(ValueError):
        parse_records('<!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>', 'test', SINCE, NOW)


@pytest.mark.asyncio
async def test_six_hour_gap_and_bounded_catchup():
    result = SimpleNamespace(scalar_one_or_none=lambda: SINCE)
    session = SimpleNamespace(begin=lambda: AsyncMock(), execute=AsyncMock(return_value=result))
    with patch('backend.ingestion.catchup.utc_now', return_value=NOW):
        since, until, gap = await inspect_downtime_gap(session)
        assert (since, until, gap) == (SINCE, NOW, 6)
        with patch('backend.ingestion.catchup.ingest_window', new_callable=AsyncMock) as ingest:
            ingest.return_value = {'inserted_count': 1, 'published_count': 1}
            summary = await run_catchup_backfill(session, object(), [])
            assert summary['gap_hours'] == 6
            assert ingest.call_args.kwargs == {'backfill': True}
    with patch('backend.ingestion.catchup.utc_now', return_value=NOW + timedelta(days=9)):
        assert (await inspect_downtime_gap(session))[2] == 48
