# @file: backend/spatial/worker.py
# @exports: SpatialService, SpatialBusy, main
# @depends_on: backend/spatial/{pipeline,geocoder}.py
# @purpose: Serialized API drain or native worker with shared rate-limited geocoder.
import argparse
import asyncio

from backend.core.config import get_settings
from backend.core.database import create_database
from backend.core.redis_bus import RedisStreamBus
from backend.spatial.geocoder import Geocoder
from backend.spatial.pipeline import process_extracted_stream_batch


class SpatialBusy(Exception):
    pass


class SpatialService:
    def __init__(self, session_factory, bus, settings=None):
        self.session_factory, self.bus = session_factory, bus
        self.settings = settings or get_settings()
        self.geocoder = Geocoder(self.settings, redis=bus.client)
        self.lock = asyncio.Lock()

    async def process(self):
        if self.lock.locked():
            raise SpatialBusy('Spatial drain already running')
        totals = dict(geocoded_count=0, cache_hits=0, nominatim_hits=0, clusters_created_or_updated=0,
                      geocode_ms=0.0, dead_letter_count=0, deferred_count=0, handled_count=0, published_count=0)
        async with self.lock:
            for _ in range(self.settings.spatial_max_batches):
                async with self.session_factory() as session:
                    stats = await process_extracted_stream_batch(session, self.bus, self.geocoder,
                                                                 count=self.settings.spatial_batch_size)
                for key in totals:
                    totals[key] += stats[key]
                if not stats['handled_count'] or stats['deferred_count']:
                    break
        elapsed = totals.pop('geocode_ms')
        totals['avg_geocode_ms'] = elapsed/totals['geocoded_count'] if totals['geocoded_count'] else 0.0
        return totals


async def run(watch):
    settings = get_settings()
    engine, factory = create_database(settings)
    bus = RedisStreamBus.from_url(settings.redis_url.get_secret_value())
    service = SpatialService(factory, bus, settings)
    try:
        while True:
            print(await service.process())
            if not watch:
                break
            await asyncio.sleep(1)
    finally:
        await bus.close()
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--watch', action='store_true')
    asyncio.run(run(parser.parse_args().watch))


if __name__ == '__main__':
    main()
