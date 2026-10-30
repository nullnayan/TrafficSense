# @file: backend/ingestion/macro_sentry.py
# @exports: execute_macro_sentry_cycle, MacroSentryRuntime, CycleBusy
# @depends_on: backend/ingestion/{catchup,connectors,pipeline}.py
# @purpose: Startup catch-up followed by nonoverlapping two-hour/manual polling lifecycle.
import asyncio
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.core.config import get_settings
from backend.ingestion.catchup import inspect_downtime_gap, run_catchup_backfill
from backend.ingestion.connectors import build_connectors
from backend.ingestion.pipeline import ingest_window
from backend.ingestion.providers import provider_status

logger = logging.getLogger(__name__)


class CycleBusy(Exception):
    pass


async def execute_macro_sentry_cycle(session, bus, connectors):
    settings = get_settings()
    since, until, _ = await inspect_downtime_gap(session)
    # Overlap the last poll window so delayed feed arrivals are retried idempotently.
    since = min(since, until - timedelta(hours=settings.macro_sentry_poll_hours))
    since = max(since, until - timedelta(hours=settings.ingestion_max_lookback_hours))
    return await ingest_window(session, bus, connectors, since, until)


class MacroSentryRuntime:
    def __init__(self, session_factory, bus, settings, connectors=None):
        self.session_factory, self.bus, self.settings = session_factory, bus, settings
        self.connectors = connectors if connectors is not None else build_connectors(settings)
        self.scheduler = AsyncIOScheduler(timezone='UTC')
        self.lock = asyncio.Lock()
        self.bootstrap_task = None
        self.last_summary = None

    def start(self):
        self.bootstrap_task = asyncio.create_task(self._bootstrap())

    async def _bootstrap(self):
        try:
            self.last_summary = await self.trigger()
        except Exception:
            logger.exception('Macro-Sentry startup attempt failed; scheduled cycle will retry')
        self.scheduler.add_job(
            self._scheduled, 'interval', hours=self.settings.macro_sentry_poll_hours,
            id='national_macro_sentry', max_instances=1, coalesce=True, misfire_grace_time=300,
        )
        self.scheduler.start()

    async def _scheduled(self):
        try:
            await self.trigger()
        except CycleBusy:
            logger.info('Macro-Sentry cycle already running')

    async def trigger(self):
        if self.lock.locked():
            raise CycleBusy('Macro-Sentry cycle already running')
        async with self.lock:
            async with self.session_factory() as session:
                summary = await run_catchup_backfill(session, self.bus, self.connectors)
                if summary.get('skipped'):
                    summary = await execute_macro_sentry_cycle(session, self.bus, self.connectors)
                self.last_summary = summary
                summary['active_providers'] = provider_status(self.settings, self.connectors, summary)
                return summary

    async def stop(self):
        if self.bootstrap_task and not self.bootstrap_task.done():
            self.bootstrap_task.cancel()
            await asyncio.gather(self.bootstrap_task, return_exceptions=True)
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            # AsyncIOExecutor cancels scheduled tasks; wait for their transaction cleanup.
            await asyncio.sleep(0)
        async with self.lock:
            pass
