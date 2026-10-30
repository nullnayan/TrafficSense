# @file: tests/test_omnichannel_pipeline.py
# @exports: Provider configuration, isolated deadline and durable fingerprint tests
# @depends_on: backend/ingestion/{providers,pipeline,deduplication,storage}.py
# @purpose: Ensure concurrent failures, fixture isolation and cross-cycle content/URL idempotency.
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import text

from backend.core.config import Settings
from backend.ingestion.connectors import build_connectors
from backend.ingestion.deduplication import deduplicate, normalized_url
from backend.ingestion.pipeline import ingest_window
from backend.ingestion.providers import omnichannel_connectors, provider_status
from backend.ingestion.storage import persist_items
from backend.main import app
from backend.schemas.ingestion import RawIngestedItem
from tests.test_omnichannel_news import NOW, SINCE


def item(url='https://news.example/a', text='Road blocked by landslide'):
    return RawIngestedItem(source_name='test', source_type='test', source_url=url,
                           title='NH-7 road blocked', raw_text=text, published_at=NOW)


def test_all_keys_independently_activate_and_fixtures_isolate():
    fields = ['tomtom', 'geoapify', 'ors', 'serper', 'tavily', 'youtube', 'newsdata', 'currents', 'gnews', 'data_gov_in']
    settings = Settings(**{name+'_api_key': 'test-only' for name in fields},
                        data_gov_resource_ids=['resource-id'], ingestion_source_mode='live')
    sources = build_connectors(settings)
    names = [source.name for source in sources]
    assert len(names) == len(set(names)) and 'tomtom_incidents' in names and 'data_gov_in_resource-id' in names
    status = provider_status(settings, sources)
    assert all(status[name+'_api']['enabled'] for name in fields)
    assert 'test-only' not in str(status)
    assert not omnichannel_connectors(settings.model_copy(update={'ingestion_source_mode': 'fixtures'}))
    disabled = settings.model_copy(update={'direct_scrapers_enabled': False})
    assert not any('syndication' in source.name for source in omnichannel_connectors(disabled))
    assert len(omnichannel_connectors(Settings(_env_file=None, direct_scrapers_enabled=False))) == 0


def test_url_and_content_deduplication():
    first = item('http://www.news.example/a/?utm_source=x#part')
    assert normalized_url(first.source_url) == 'https://news.example/a'
    assert len(deduplicate([first, item('https://news.example/a?gclid=q'), item('https://other.example/b')])) == 1
    assert len(deduplicate([first, item('https://other.example/b', 'Different road blocked')])) == 2
    assert normalized_url('https://youtube.com/watch?v=abc&utm_medium=a').endswith('?v=abc')
    assert normalized_url('https://twitter.com/handle/status/1') == 'https://x.com/handle/status/1'


@pytest.mark.asyncio
async def test_canonical_health_exposes_provider_status():
    payload = {'status': 'ok', 'active_providers': {'gnews_api': {
        'enabled': True, 'returned_count': 2, 'status': 'ok'}}}
    with patch('backend.main.dependency_health', new=AsyncMock(return_value=payload)):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.get('/api/v1/health')
    assert response.status_code == 200 and response.json()['active_providers']['gnews_api']['returned_count'] == 2


@pytest.mark.asyncio
async def test_concurrent_deadline_failure_isolation(monkeypatch):
    monkeypatch.setattr('backend.ingestion.pipeline.SOURCE_TIMEOUT_SECONDS', .02)
    async def slow(*args):
        await asyncio.sleep(1)
    sources = [SimpleNamespace(name=name, mode='live', last_error=None, invalid_count=0,
                truncated=False, used_fixture=False, fetch=fetch) for name, fetch in [
        ('slow', slow), ('error', AsyncMock(side_effect=ValueError)), ('healthy', AsyncMock(return_value=[item()]))]]
    session = SimpleNamespace(begin=lambda: AsyncMock(), rollback=AsyncMock(),
                              execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: 0)))
    with patch('backend.ingestion.pipeline.persist_items', new=AsyncMock(return_value=(1, 0))), \
         patch('backend.ingestion.pipeline.flush_outbox', new=AsyncMock(return_value=(1, 1))), \
         patch('backend.ingestion.pipeline.write_heartbeat', new=AsyncMock()):
        summary = await ingest_window(session, object(), sources, SINCE, NOW)
    assert summary['active_providers']['slow']['status'] == 'TimeoutError'
    assert summary['active_providers']['healthy']['returned_count'] == 1
    assert summary['inserted_count'] == 1 and summary['status'] == 'degraded'


@pytest.mark.asyncio
async def test_persistent_fingerprints_across_sources_and_cycles(ingestion_services):
    factory, _ = ingestion_services
    async with factory() as session:
        assert (await persist_items(session, [item()]))[0] == 1
        assert (await persist_items(session, [item('https://other.example/report')]))[0] == 0
        assert (await persist_items(session, [item('http://www.news.example/a/?utm_source=x')]))[0] == 0
        assert (await session.execute(text('SELECT count(*) FROM raw_reports'))).scalar_one() == 1
