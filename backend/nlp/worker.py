# @file: backend/nlp/worker.py
# @exports: NLPService, NLPBusy, run_worker
# @depends_on: backend/nlp/pipeline.py
# @purpose: Bounded manual drain or native continuous worker using shared model instances.
import asyncio

from backend.core.config import get_settings
from backend.core.database import create_database
from backend.core.redis_bus import RedisStreamBus
from backend.nlp.extractor import IncidentExtractor
from backend.nlp.gatekeeper_model import GatekeeperModel
from backend.nlp.pipeline import process_filtered_stream_batch, process_raw_stream_batch


class NLPBusy(Exception):
    pass


class NLPService:
    def __init__(self, session_factory, bus, settings=None):
        self.session_factory, self.bus = session_factory, bus
        self.settings = settings or get_settings()
        self.classifier, self.extractor = GatekeeperModel(self.settings), IncidentExtractor(self.settings)
        self.lock = asyncio.Lock()

    async def process(self):
        if self.lock.locked():
            raise NLPBusy('NLP drain already running')
        totals = dict(processed_raw=0, active_forwarded=0, dropped_noise_or_inactive=0,
                      extracted_count=0, dead_letter_count=0, deferred_count=0, handled_count=0)
        async with self.lock:
            for process in (process_raw_stream_batch, process_filtered_stream_batch):
                for _ in range(self.settings.nlp_max_batches):
                    async with self.session_factory() as session:
                        stats = await process(session, self.bus, classifier=self.classifier,
                                              extractor=self.extractor, count=self.settings.nlp_batch_size)
                    for key in totals:
                        totals[key] += stats[key]
                    if not stats['handled_count'] or stats['deferred_count']:
                        break
        return totals


async def run_worker(watch=False):
    settings = get_settings()
    engine, factory = create_database(settings)
    bus = RedisStreamBus.from_url(settings.redis_url.get_secret_value())
    service = NLPService(factory, bus, settings)
    try:
        while True:
            print(await service.process())
            if not watch:
                break
            await asyncio.sleep(1)
    finally:
        await bus.close()
        await engine.dispose()
