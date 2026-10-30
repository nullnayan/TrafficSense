# @file: backend/spatial/storage.py
# @exports: prepare_spatial, deliver_spatial, mark_failed, Ungeocodable
# @depends_on: backend/spatial/{geocoder,clustering,records}.py
# @purpose: Transactional geocode/cluster state, strict event outbox, and replay-safe delivery.
import json

from sqlalchemy import text

from backend.core.redis_bus import StreamNames
from backend.schemas.extraction import ExtractedIncidentSchema
from backend.schemas.spatial import GeocodedLocation, SpatialReportEnvelope
from backend.spatial.clustering import cluster_report
from backend.spatial.records import read_golden


class Ungeocodable(ValueError):
    pass


def attributes_of(row):
    values = row['extracted_attributes']
    return (ExtractedIncidentSchema.model_validate_json(values) if isinstance(values, str)
            else ExtractedIncidentSchema.model_validate(values))


async def prepare_spatial(session, report_id, geocoder):
    stats = dict(geocoded_count=0, cache_hits=0, geoapify_hits=0, nominatim_hits=0, clusters_created_or_updated=0, geocode_ms=0.0)
    async with session.begin():
        row = (await session.execute(text('''
            SELECT *, ST_X(geom) AS longitude, ST_Y(geom) AS latitude FROM raw_reports WHERE report_id=:id
        '''), {'id': report_id})).mappings().one_or_none()
        if row is None:
            raise ValueError('Missing extracted report')
        if row['pipeline_status'] == 'CLUSTERED':
            return stats
        if row['pipeline_status'] not in ('EXTRACTED','extracted','GEOCODED','GEOCODE_FAILED'):
            raise ValueError('Spatial stage requires an extracted report')
        attributes = attributes_of(row)
        if attributes.report_id != report_id or attributes.temporal_status != 'active':
            raise ValueError('Only active, identity-consistent extraction can be geocoded')
        location = None
        if row['pipeline_status'] != 'GEOCODED' or row['longitude'] is None:
            location = await geocoder.geocode(session, attributes.landmark_tuple)
            if location is None:
                raise Ungeocodable('No sufficiently confident Indian landmark')
        else:
            location = GeocodedLocation(longitude=row['longitude'], latitude=row['latitude'],
                method=row['geocode_method'], confidence=row['geocode_confidence'])
        # Lock ordering is global clustering lock, then report row; never the reverse.
        await session.execute(text('SELECT pg_advisory_xact_lock(707202604)'))
        state = (await session.execute(text('SELECT pipeline_status FROM raw_reports WHERE report_id=:id FOR UPDATE'),
                                       {'id': report_id})).scalar_one()
        if state == 'CLUSTERED':
            return stats
        await session.execute(text('''
            UPDATE raw_reports SET geom=ST_SetSRID(ST_MakePoint(:lon,:lat),4326),
                geocode_method=:method,geocode_confidence=:confidence,pipeline_status='GEOCODED'
            WHERE report_id=:id
        '''), {'id': report_id, 'lon': location.longitude, 'lat': location.latitude,
               'method': str(location.method), 'confidence': location.confidence})
        cluster = await cluster_report(session, report_id, attributes, geocoder.settings)
        golden = await read_golden(session, cluster['cluster_id'])
        payload = SpatialReportEnvelope(report_id=report_id, geocoded_location=location,
                                         golden_record=golden, **{k: v for k, v in cluster.items() if k != 'report_count'})
        await session.execute(text('''
            INSERT INTO spatial_stage_outbox (report_id,cluster_id,payload)
            VALUES (:id,:cluster,CAST(:payload AS jsonb)) ON CONFLICT DO NOTHING
        '''), {'id': report_id, 'cluster': cluster['cluster_id'], 'payload': payload.model_dump_json()})
        stats.update(geocoded_count=int(row['pipeline_status'] != 'GEOCODED'),
            cache_hits=int(location.method == 'gazetteer'), geoapify_hits=int(location.method == 'geoapify'),
            nominatim_hits=int(location.method == 'nominatim'),
            clusters_created_or_updated=1, geocode_ms=geocoder.last_total_ms)
    return stats


async def deliver_spatial(session, bus, report_id):
    async with session.begin():
        row = (await session.execute(text('''
            SELECT payload,delivered_at FROM spatial_stage_outbox WHERE report_id=:id FOR UPDATE
        '''), {'id': report_id})).mappings().one_or_none()
        if row is None or row['delivered_at'] is not None:
            return False
        values = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
        envelope = SpatialReportEnvelope.model_validate(values)
        _, fresh = await bus.publish_once(StreamNames.GEOCODED_ITEMS, envelope, str(report_id))
        await session.execute(text('UPDATE spatial_stage_outbox SET delivered_at=now() WHERE report_id=:id'),
                              {'id': report_id})
    return fresh


async def mark_failed(session, report_id):
    if report_id is not None:
        async with session.begin():
            await session.execute(text('''
                UPDATE raw_reports SET pipeline_status='GEOCODE_FAILED'
                WHERE report_id=:id AND pipeline_status IN ('EXTRACTED','GEOCODED','GEOCODE_FAILED')
            '''), {'id': report_id})
