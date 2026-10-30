# @file: backend/analytics/worker.py
# @exports: AnalyticsBusy, AnalyticsService, main
# @depends_on: backend/analytics/pipeline.py, backend/core/{config,database,redis_bus}.py
# @purpose: Serialized bounded API drain and native one-shot or continuous clearance worker.
import argparse
import asyncio
import json
from uuid import uuid4

from backend.analytics.pipeline import process_geocoded_stream_batch
from backend.core.config import get_settings
from backend.core.database import create_database
from backend.core.redis_bus import RedisStreamBus


class AnalyticsBusy(RuntimeError):
    pass


class AnalyticsService:
    def __init__(self, session_factory, bus, settings):
        self.session_factory, self.bus, self.settings = session_factory, bus, settings
        self.lock = asyncio.Lock()
        self.consumer = f'analytics-{uuid4()}'
        self.reclaim_cursor = '0-0'

    async def process(self):
        if self.lock.locked():
            raise AnalyticsBusy('Analytics drain already running')
        async with self.lock:
            totals = dict.fromkeys(('clusters_analyzed', 'level1_count', 'level2_count', 'level3_count',
                                    'clusters_expired', 'dead_letter_count', 'processed_count', 'published_count'), 0)
            expected_sum = 0.0
            async with self.session_factory() as session:
                for _ in range(self.settings.analytics_max_batches):
                    result = await process_geocoded_stream_batch(session, self.bus, self.consumer,
                        self.settings.analytics_batch_size, self.settings.analytics_max_attempts,
                        self.settings.analytics_include_seed, self.reclaim_cursor)
                    self.reclaim_cursor = result['reclaim_cursor']
                    for name in totals:
                        totals[name] += result[name]
                    expected_sum += result['expected_clearance_sum']
                    if (result['processed_count'] == 0 and result['outbox_processed_count'] == 0
                            and self.reclaim_cursor == '0-0'):
                        break
            totals['avg_expected_clearance_min'] = expected_sum / totals['clusters_analyzed'] if totals[
                'clusters_analyzed'] else 0.0
            return totals


async def run_worker(watch=False):
    settings = get_settings()
    engine, factory = create_database(settings)
    bus = RedisStreamBus.from_url(settings.redis_url.get_secret_value())
    service = AnalyticsService(factory, bus, settings)
    try:
        while True:
            try:
                print(json.dumps(await service.process()))
            except Exception:
                if not watch:
                    raise
                print(json.dumps({'status': 'retrying', 'pending_retained': True}))
            if not watch:
                break
            await asyncio.sleep(5)
    finally:
        await bus.close()
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description='Drain PCU clearance analytics')
    parser.add_argument('--watch', action='store_true')
    asyncio.run(run_worker(parser.parse_args().watch))


if __name__ == '__main__':
    main()
