# @file: tests/test_spatial_tools.py
# @exports: Explicit demo idempotency and local benchmark artifact tests
# @depends_on: backend/spatial/{demo,benchmark}.py, tests/spatial_support.py
# @purpose: Validate the reproducible viva dataset and measured-query report using isolated PostGIS.
import pytest
from spatial_support import process_all
from spatial_support import spatial_services as _spatial_services
from sqlalchemy import text

from backend.spatial.benchmark import benchmark
from backend.spatial.demo import seed_demo
from backend.spatial.records import list_active

spatial_services = _spatial_services


@pytest.mark.asyncio
async def test_explicit_demo_seed_is_idempotent_and_scoped(spatial_services):
    factory, bus = spatial_services
    assert await seed_demo(factory, bus) == {'fixture_inserted': 3, 'fixture_published': 3}
    assert await seed_demo(factory, bus) == {'fixture_inserted': 0, 'fixture_published': 0}
    stats = await process_all(factory, bus)
    assert stats['geocoded_count'] == stats['cache_hits'] == 3
    async with factory() as session:
        records = await list_active(session)
        assert len(records) == 1 and records[0].report_count == 3
        assert records[0].lanes_blocked == 2 and records[0].source_scope == 'fixture'
        assert await list_active(session, include_fixtures=False) == []
        assert (await session.execute(text('SELECT count(*) FROM raw_reports'))).scalar_one() == 3


@pytest.mark.asyncio
async def test_benchmark_reports_scope_and_index_capability(spatial_services):
    factory, _ = spatial_services
    report = await benchmark(factory, repeats=2)
    assert report['gazetteer_count'] == 10 and report['repeats_per_query'] == 2
    assert report['warm_mean_sql_ms'] >= 0 and len(report['samples']) == 2
    assert report['trigram_index_plan_with_seqscan_disabled'] is True
