# @file: backend/spatial/benchmark.py
# @exports: benchmark, main
# @depends_on: backend/spatial/geocoder.py
# @purpose: Record real local-query timings and explain trigram-index capability without claiming national-scale performance.
import argparse
import asyncio
import json
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from sqlalchemy import text

from backend.core.config import ROOT, get_settings
from backend.core.database import create_database
from backend.schemas.extraction import LandmarkTuple
from backend.spatial.geocoder import CANDIDATES, Geocoder
from backend.spatial.normalization import city_names, contextualize, normalize, query_candidates, state_names


async def benchmark(factory, repeats=20):
    if not 1 <= repeats <= 1000:
        raise ValueError('repeats must be 1..1000')
    geocoder = Geocoder(get_settings().model_copy(update={'nominatim_enabled': False}))
    samples = ['Nr Clock Twr Chowk Rajpur Rd Dehradun', 'Laxman Jhula NH7 Rishikesh']
    timings, results = [], []
    async with factory() as session, session.begin():
        count = (await session.execute(text('SELECT count(*) FROM gazetteer_places'))).scalar_one()
        baseline = []
        for _ in range(5):
            started = perf_counter()
            await session.execute(text('SELECT 1'))
            baseline.append((perf_counter()-started)*1000)
        for name in samples:
            initial = await geocoder.geocode(session, LandmarkTuple(primary_landmark=name))
            if initial is None:
                raise ValueError(f'Benchmark landmark unresolved: {name}')
            warm = []
            for _ in range(repeats):
                result = await geocoder.geocode(session, LandmarkTuple(primary_landmark=name))
                warm.append(result.query_latency_ms)
                timings.append(result.query_latency_ms)
            results.append({'query': name, 'resolved_name': initial.display_name, 'geoname_id': initial.geoname_id,
                'confidence': initial.confidence, 'cold_sql_ms': initial.query_latency_ms,
                'warm_mean_sql_ms': mean(warm), 'warm_median_sql_ms': median(warm)})
        landmark = contextualize(LandmarkTuple(primary_landmark=samples[0]))
        key = normalize(' '.join(filter(None, [landmark.primary_landmark, landmark.corridor_name,
                                               landmark.locality, landmark.city, landmark.state])))
        parameters = {
            'queries': query_candidates(landmark), 'key': key, 'state': normalize(landmark.state),
            'states': state_names(landmark.state), 'city': normalize(landmark.city),
            'cities': city_names(landmark.city), 'city_pattern': '%'+normalize(landmark.city)+'%',
        }
        actual = (await session.execute(text('EXPLAIN (ANALYZE, FORMAT JSON) '+str(CANDIDATES)), parameters)).scalar_one()[0]
        await session.execute(text('SET LOCAL enable_seqscan=off'))
        plan = (await session.execute(text('EXPLAIN (FORMAT JSON) '+str(CANDIDATES)), parameters)).scalar_one()
        gin_names = (await session.execute(text('''SELECT i.relname FROM pg_index x
            JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_am a ON a.oid=i.relam
            WHERE x.indrelid='gazetteer_places'::regclass AND a.amname='gin'
        '''))).scalars().all()
    return {'gazetteer_count': count, 'samples': results, 'repeats_per_query': repeats,
        'warm_mean_sql_ms': mean(timings), 'warm_median_sql_ms': median(timings),
        'warm_mean_under_5ms': mean(timings) < 5,
        'select_one_roundtrip_mean_ms': mean(baseline),
        'explain_server_execution_ms': actual['Execution Time'], 'explain_planning_ms': actual['Planning Time'],
        'trigram_index_plan_with_seqscan_disabled': any(name in json.dumps(plan) for name in gin_names),
        'limitations': 'Small development dataset; warmed local SQL timings include round trips, not national-scale validation.'}


async def run(args):
    engine, factory = create_database(get_settings())
    try:
        report = await benchmark(factory, args.repeats)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--output', type=Path, default=ROOT/'benchmarks/spatial_lookup.json')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
