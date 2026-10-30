# @file: tests/test_analytics_api.py
# @exports: Analytics telemetry, concurrency, dependency errors and active enrichment tests
# @depends_on: backend/main.py, backend/analytics/worker.py, tests/analytics_support.py
# @purpose: Validate Phase 5 HTTP behavior and populated Golden Records using real persistence.
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.analytics.storage import prepare_analytics
from backend.analytics.worker import AnalyticsBusy, AnalyticsService
from backend.core.config import get_settings
from backend.core.redis_bus import StreamNames
from backend.main import app
from tests.analytics_support import (
    analytics_services as analytics_services,
)
from tests.analytics_support import (
    envelope,
    golden,
    insert_cluster,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('error,status', [(None, 200), (AnalyticsBusy('Busy'), 409), (OSError('Down'), 503)])
async def test_api_telemetry_and_errors(error, status):
    telemetry = {'clusters_analyzed': 3, 'level1_count': 1, 'level2_count': 1, 'level3_count': 1,
                 'clusters_expired': 2, 'avg_expected_clearance_min': 45, 'dead_letter_count': 0}
    service = SimpleNamespace(process=AsyncMock(return_value=telemetry, side_effect=error))
    with patch.object(app.state, 'analytics', service, create=True):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.post('/api/v1/analytics/process')
    assert response.status_code == status
    if status == 200:
        assert response.json() == telemetry


@pytest.mark.asyncio
async def test_service_lock():
    service = AnalyticsService(None, None, get_settings())
    async with service.lock:
        with pytest.raises(AnalyticsBusy):
            await service.process()


@pytest.mark.asyncio
async def test_http_drain_enriched_active_records_and_fixture_filter(analytics_services):
    factory, bus = analytics_services
    now = datetime.now(timezone.utc)
    record = await insert_cluster(factory, golden(first_reported_at=now, last_updated_at=now,
                                                  last_reported_at=now))
    await bus.publish(StreamNames.GEOCODED_ITEMS, envelope(record))
    service = AnalyticsService(factory, bus, get_settings())
    with patch.object(app.state, 'analytics', service, create=True), patch.object(
            app.state, 'session_factory', factory, create=True):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            result = (await client.post('/api/v1/analytics/process')).json()
            assert result['clusters_analyzed'] == result['level3_count'] == 1
            active = (await client.get('/api/v1/incidents/active')).json()
            assert len(active) == 1 and active[0]['expected_clearance_minutes'] > 0
            assert active[0]['waterfall_breakdown']['lane_input_mode'] == 'abstained_severity_prior'
            assert active[0]['lanes_blocked'] is None and active[0]['total_lanes'] is None
            assert active[0]['estimated_clear_at'] == active[0]['predicted_clear_at']
            assert (await client.get('/api/v1/incidents/active?include_fixtures=false')).json() == []
            assert (await client.post('/api/v1/analytics/process')).json()['clusters_analyzed'] == 0


@pytest.mark.asyncio
async def test_drain_recovers_outbox_backlog_without_source_messages(analytics_services):
    factory, bus = analytics_services
    now = datetime.now(timezone.utc)
    async with factory() as session:
        for _ in range(3):
            record = await insert_cluster(factory, golden(first_reported_at=now, last_updated_at=now,
                                                          last_reported_at=now))
            await prepare_analytics(session, envelope(record))
    settings = get_settings().model_copy(update={'analytics_batch_size': 1, 'analytics_max_batches': 10})
    result = await AnalyticsService(factory, bus, settings).process()
    assert result['published_count'] == 3 and result['processed_count'] == 0
    assert await bus.client.xlen(StreamNames.ANALYZED_ITEMS) == 3
