# @file: tests/test_spatial_pipeline.py
# @exports: Opt-in fuzzy cache, metric/time clustering, Golden Record, and replay tests
# @depends_on: backend/spatial, tests/spatial_support.py
# @purpose: Prove real PostGIS thresholds, URL-independent fusion, fallback caching and durable handoff.
from unittest.mock import patch

import httpx
import pytest
from spatial_support import insert_extracted, process_all
from spatial_support import spatial_services as _spatial_services
from sqlalchemy import text

from backend.core.config import ROOT, get_settings
from backend.core.redis_bus import StreamNames
from backend.schemas.extraction import LandmarkTuple
from backend.spatial.gazetteer_loader import import_gazetteer
from backend.spatial.geocoder import Geocoder
from backend.spatial.records import list_active

spatial_services = _spatial_services


@pytest.mark.asyncio
async def test_fuzzy_landmarks_and_latency(spatial_services):
    factory, _ = spatial_services
    geocoder = Geocoder(get_settings().model_copy(update={'nominatim_enabled': False}))
    async with factory() as session, session.begin():
        await session.execute(text('''INSERT INTO gazetteer_places(geoname_id,name,ascii_name,city_name,admin1_state,geom)
            VALUES (99999,'Clock Tower','Clock Tower','Delhi','Delhi',ST_SetSRID(ST_MakePoint(77.2,28.6),4326))'''))
    for query, latitude in [('Nr Clock Twr Chowk Rajpur Rd Dehradun', 30.3256),
                            ('Laxman Jhula NH7 Rishikesh', 30.1264)]:
        async with factory() as session, session.begin():
            location = await geocoder.geocode(session, LandmarkTuple(primary_landmark=query))
        assert location and abs(location.latitude-latitude) < 0.001
        assert location.method == 'gazetteer' and location.query_latency_ms > 0


@pytest.mark.asyncio
async def test_regional_loader_is_idempotent(spatial_services):
    factory, _ = spatial_services
    for _ in range(2):
        async with factory() as session:
            assert (await import_gazetteer(session, ROOT/'data/regional_gazetteer.csv', batch_size=5))['upserted_count'] == 24
    async with factory() as session:
        assert (await session.execute(text('SELECT count(*) FROM gazetteer_places'))).scalar_one() == 34


@pytest.mark.asyncio
async def test_three_reports_merge_and_preserve_partial_lanes(spatial_services):
    factory, bus = spatial_services
    await insert_extracted(factory, bus, minutes=0, metres=0)
    await process_all(factory, bus)
    async with factory() as session:
        old = (await list_active(session))[0].attribute_completeness_score
    await insert_extracted(factory, bus, minutes=20, metres=200, blocked=2)
    await insert_extracted(factory, bus, minutes=40, metres=450)
    await process_all(factory, bus)
    async with factory() as session:
        records = await list_active(session)
    assert len(records) == 1 and records[0].report_count == 3
    assert records[0].lanes_blocked == 2 and records[0].total_lanes is None
    assert records[0].attribute_completeness_score > old
    assert len(set(records[0].source_report_ids)) == 3
    assert records[0].impact_polygon_geojson['type'] == 'Polygon'
    await insert_extracted(factory, bus, minutes=90, metres=0)
    await insert_extracted(factory, bus, minutes=20, metres=1200)
    await process_all(factory, bus)
    async with factory() as session:
        assert len(await list_active(session)) == 3


@pytest.mark.asyncio
async def test_metric_499_metres_and_nonchaining_time_window(spatial_services):
    factory, bus = spatial_services
    for minute, distance in [(0, 0), (45, 499), (90, 499)]:
        await insert_extracted(factory, bus, minutes=minute, metres=distance)
        await process_all(factory, bus)
    async with factory() as session:
        records = await list_active(session)
    assert sorted(record.report_count for record in records) == [1, 2]


@pytest.mark.asyncio
async def test_nominatim_hit_is_cached_for_next_lookup(spatial_services):
    factory, _ = spatial_services
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[{'lat': '30.33', 'lon': '78.05', 'display_name': 'New Pilgrim Bridge',
            'address': {'country_code': 'in', 'city': 'Dehradun', 'state': 'Uttarakhand'}}])
    settings = get_settings().model_copy(update={'nominatim_enabled': True})
    landmark = LandmarkTuple(primary_landmark='New Pilgrim Bridge', city='Dehradun', state='Uttarakhand')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        geocoder = Geocoder(settings, client)
        async with factory() as session, session.begin():
            first = await geocoder.geocode(session, landmark)
        async with factory() as session, session.begin():
            second = await geocoder.geocode(session, landmark)
    assert first.method == 'nominatim' and second.method == 'gazetteer'
    assert first.geoname_id == second.geoname_id < 0 and len(calls) == 1


@pytest.mark.asyncio
async def test_publish_rollback_replays_without_reclustering(spatial_services):
    factory, bus = spatial_services
    await insert_extracted(factory, bus)
    original = bus.publish_once
    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError('Crash after Redis success')
    with patch.object(bus, 'publish_once', side_effect=crash):
        assert (await process_all(factory, bus))['deferred_count'] == 1
    replay = await process_all(factory, bus)
    assert replay['clusters_created_or_updated'] == replay['geocoded_count'] == 0
    assert await bus.client.xlen(StreamNames.GEOCODED_ITEMS) == 1
    assert (await bus.client.xpending(StreamNames.EXTRACTED_ITEMS, 'spatial_geocode_group'))['pending'] == 0


@pytest.mark.asyncio
async def test_ungeocodable_report_is_failed_and_dead_lettered(spatial_services):
    factory, bus = spatial_services
    item = await insert_extracted(factory, bus, landmark=LandmarkTuple(primary_landmark='QZXV Unknown Location',
                                                                                  city='Unknown City'))
    result = await process_all(factory, bus)
    assert result['dead_letter_count'] == 1
    async with factory() as session:
        status = (await session.execute(text('SELECT pipeline_status FROM raw_reports WHERE report_id=:id'),
                                        {'id': item.report_id})).scalar_one()
    assert status == 'GEOCODE_FAILED'


@pytest.mark.asyncio
async def test_spatial_bridge_merges_clusters_and_deactivates_superseded(spatial_services):
    factory, bus = spatial_services
    for minute, distance in [(0, 0), (20, 900), (30, 450)]:
        await insert_extracted(factory, bus, minutes=minute, metres=distance)
        await process_all(factory, bus)
    async with factory() as session:
        records = await list_active(session)
        inactive = (await session.execute(text('SELECT count(*) FROM incident_clusters WHERE NOT is_active'))).scalar_one()
    assert len(records) == 1 and records[0].report_count == 3 and inactive == 1


@pytest.mark.asyncio
async def test_exact_time_boundary_and_category_separation(spatial_services):
    factory, bus = spatial_services
    for minute in [0, 60, 60+1/60]:
        await insert_extracted(factory, bus, minutes=minute, metres=0)
        await process_all(factory, bus)
    await insert_extracted(factory, bus, minutes=0, metres=0, kind='collision')
    await process_all(factory, bus)
    async with factory() as session:
        records = await list_active(session)
    assert sorted(record.report_count for record in records) == [1, 1, 2]
