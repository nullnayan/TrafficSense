# @file: tests/test_ingestion_runtime.py
# @exports: Runtime scheduling, lifecycle, and manual-trigger API tests
# @depends_on: backend/ingestion/macro_sentry.py, backend/main.py
# @purpose: Ensure catch-up precedes scheduling and API controls serialize execution.
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.ingestion.macro_sentry import CycleBusy, MacroSentryRuntime
from backend.main import app


@pytest.mark.asyncio
async def test_startup_catchup_precedes_scheduler_and_manual_poll():
    session = object()
    factory = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=session)))
    settings = SimpleNamespace(macro_sentry_poll_hours=2)
    runtime = MacroSentryRuntime(factory, object(), settings, connectors=[])
    runtime.scheduler = MagicMock(running=False)
    with patch('backend.ingestion.macro_sentry.run_catchup_backfill', new_callable=AsyncMock) as catchup, \
         patch('backend.ingestion.macro_sentry.execute_macro_sentry_cycle', new_callable=AsyncMock) as poll:
        catchup.return_value = {'backfill': True, 'published_count': 1}
        runtime.start()
        await runtime.bootstrap_task
        catchup.assert_awaited_once()
        poll.assert_not_awaited()
        kwargs = runtime.scheduler.add_job.call_args.kwargs
        assert kwargs['hours'] == 2 and kwargs['max_instances'] == 1 and kwargs['coalesce']
        catchup.return_value = {'skipped': True}
        poll.return_value = {'published_count': 0}
        assert (await runtime.trigger())['published_count'] == 0
        async with runtime.lock:
            with pytest.raises(CycleBusy):
                await runtime.trigger()
    await runtime.stop()


@pytest.mark.asyncio
async def test_shutdown_cancels_bootstrap():
    runtime = MacroSentryRuntime(None, None, SimpleNamespace(macro_sentry_poll_hours=2), connectors=[])
    async def long_trigger():
        await asyncio.sleep(100)
    with patch.object(runtime, 'trigger', side_effect=long_trigger):
        runtime.start()
        await asyncio.sleep(0)
        await runtime.stop()
    assert runtime.bootstrap_task.cancelled()
    assert not runtime.scheduler.running


@pytest.mark.asyncio
@pytest.mark.parametrize('state,code', [('disabled', 503), ('busy', 409), ('success', 200)])
async def test_trigger_endpoint(state, code):
    runtime = None if state == 'disabled' else SimpleNamespace(trigger=AsyncMock())
    if state == 'busy':
        runtime.trigger.side_effect = CycleBusy('Cycle busy')
    elif runtime:
        runtime.trigger.return_value = {'fetched_count': 4, 'inserted_count': 3,
                                        'published_count': 2, 'dropped_noise_count': 1}
    with patch.object(app.state, 'ingestion', runtime, create=True):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.post('/api/v1/ingest/trigger')
    assert response.status_code == code
    if code == 200:
        assert response.json()['published_count'] == 2
