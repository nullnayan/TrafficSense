# @file: backend/streaming/demo_pipeline.py
# @exports: router, run_demo, main
# @depends_on: backend/ingestion/storage.py, backend/{nlp,spatial,analytics}/worker.py
# @purpose: Idempotent synthetic raw-report ingestion through all four durable pipeline stages.
import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException, Request

from backend.analytics.worker import AnalyticsService
from backend.core.config import ROOT, get_settings
from backend.core.database import create_database
from backend.core.redis_bus import RedisStreamBus
from backend.ingestion.storage import flush_outbox, persist_items
from backend.nlp.worker import NLPService
from backend.schemas.ingestion import RawIngestedItem, utc_now
from backend.spatial.gazetteer_loader import import_gazetteer
from backend.spatial.worker import SpatialService

router = APIRouter(prefix='/api/v1/pipeline', tags=['demo'])


async def ingest_demo(state):
    items = []
    for number, lanes in enumerate(('Two lanes blocked.', 'Traffic disrupted.', 'Road blocked.'), 1):
        url = f'https://fixtures.trafficsense.invalid/phase7/rajpur-v1/landslide-{number}'
        items.append(RawIngestedItem(
            report_id=uuid5(NAMESPACE_URL, url), source_name='fixture:phase7', source_url=url,
            title='Active landslide near Rajpur Road in Dehradun, Uttarakhand',
            raw_text=f'Landslide near Rajpur Road in Dehradun, Uttarakhand. {lanes} '
                     'Severe traffic disruption. Police report road remains blocked now.',
            published_at=utc_now(),
        ))
    async with state.session_factory() as session:
        gazetteer = await import_gazetteer(session, ROOT / 'data/regional_gazetteer.csv')
        await session.commit()
        storage_counts = await persist_items(session, items)
        outbox_counts = await flush_outbox(session, state.bus)
    return {'inserted': storage_counts[0], 'storage_counts': storage_counts,
            'outbox_counts': outbox_counts, 'fixture_reports': len(items), 'gazetteer': gazetteer}


async def run_demo(state, output: Path | None = ROOT / 'benchmarks/pipeline_demo.json'):
    lock = getattr(state, 'demo_lock', None)
    if lock is None:
        state.demo_lock = lock = asyncio.Lock()
    if lock.locked():
        raise HTTPException(409, 'Demo pipeline already running')
    async with lock:
        started = perf_counter()
        # Fixture extraction is deterministic; no live/cloud inference or field-accuracy claim.
        settings = state.settings.model_copy(update={'inference_mode': 'calibrated_teacher'})
        nlp = NLPService(state.session_factory, state.bus, settings)
        stages = {}
        for name, operation in [('ingest', lambda: ingest_demo(state)), ('nlp', nlp.process),
                                ('spatial', state.spatial.process), ('analytics', state.analytics.process)]:
            stage_start = perf_counter()
            telemetry = await operation()
            stages[name] = {'latency_ms': round((perf_counter() - stage_start) * 1000, 3),
                            'telemetry': telemetry}
        result = {'generated_at': utc_now().isoformat(), 'source_scope': 'fixture',
                  'provenance': 'synthetic_fixture_pipeline_not_field_validation',
                  'nlp_mode': 'calibrated_teacher', 'stages': stages,
                  'queue_scope': 'shared_development_queues; stage totals may include prior pending reports',
                  'end_to_end_latency_ms': round((perf_counter() - started) * 1000, 3)}
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2, default=str) + '\n', encoding='utf-8')
        return result


@router.post('/run-demo')
async def demo_endpoint(request: Request):
    try:
        return await run_demo(request.app.state)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, 'Demo dependencies unavailable; durable stages can be retried') from exc


async def execute(output=ROOT / 'benchmarks/pipeline_demo.json'):
    settings = get_settings()
    engine, factory = create_database(settings)
    bus = RedisStreamBus.from_url(settings.redis_url.get_secret_value())
    state = SimpleNamespace(settings=settings, session_factory=factory, bus=bus,
                            spatial=SpatialService(factory, bus, settings),
                            analytics=AnalyticsService(factory, bus, settings))
    try:
        print(json.dumps(await run_demo(state, output), indent=2, default=str))
    finally:
        await asyncio.gather(bus.close(), engine.dispose())


def main():
    parser = argparse.ArgumentParser(description='Run a replay-safe synthetic viva pipeline')
    parser.add_argument('--output', type=Path, default=ROOT / 'benchmarks/pipeline_demo.json')
    asyncio.run(execute(parser.parse_args().output))


if __name__ == '__main__':
    main()
