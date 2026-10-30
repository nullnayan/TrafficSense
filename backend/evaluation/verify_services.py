# @file: backend/evaluation/verify_services.py
# @exports: verify, main
# @depends_on: backend/scout/service.py, backend/assistant/service.py
# @purpose: Record measured fixture route/query smoke verification with provider and scope provenance.
import asyncio
import json
from time import perf_counter

from backend.assistant.service import AssistantService
from backend.core.config import ROOT, get_settings
from backend.core.database import create_database
from backend.core.redis_bus import RedisStreamBus
from backend.schemas.assistant import AssistantQueryRequest
from backend.schemas.ingestion import utc_now
from backend.schemas.spatial import RouteScoutRequest
from backend.scout.service import ScoutService


async def verify():
    settings = get_settings().model_copy(update={'osrm_enabled': False, 'assistant_cloud_slots_enabled': False})
    engine, factory = create_database(settings)
    bus = RedisStreamBus.from_url(settings.redis_url.get_secret_value())
    try:
        start = perf_counter()
        route = await ScoutService(factory, bus.client, settings).evaluate(RouteScoutRequest(
            origin='Clock Tower Dehradun', destination='Laxman Jhula Rishikesh', include_fixtures=True))
        route_ms = (perf_counter() - start) * 1000
        start = perf_counter()
        result = await AssistantService(factory, settings).query(AssistantQueryRequest(
            query='Are there active incidents near Clock Tower?', include_fixtures=True))
        report = {'generated_at': utc_now().isoformat(), 'provenance': 'local_fixture_api_verification',
                  'field_validated': False, 'scout': {'latency_ms': round(route_ms, 3),
                    'route_source': route.route_source, 'choke_count': len(route.choke_points),
                    'overlaps': [i.overlap for i in route.incidents], 'risk_level': route.risk_level},
                  'assistant': {'latency_ms': round((perf_counter() - start) * 1000, 3),
                    'template_id': result.template_id, 'slot_source': result.slot_source,
                    'bound_parameters': result.parameters.model_dump(mode='json'), 'row_count': result.row_count},
                  'limitations': 'Fixture smoke verification; live OSRM, cloud slots and field route accuracy unverified.'}
        path = ROOT / 'benchmarks/scout_assistant_verification.json'
        path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return report
    finally:
        await asyncio.gather(bus.close(), engine.dispose())


def main():
    print(json.dumps(asyncio.run(verify()), indent=2))


if __name__ == '__main__':
    main()
