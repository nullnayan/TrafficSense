# @file: backend/spatial/records.py
# @exports: read_golden, list_active, golden_from_row
# @depends_on: backend/schemas/spatial.py
# @purpose: Strict Golden Record serialization from canonical attributes and PostGIS geometries.
import json

from sqlalchemy import text

from backend.schemas.spatial import GoldenRecordSchema

SELECT_RECORD = '''SELECT c.*, ST_X(centroid_geom) AS longitude, ST_Y(centroid_geom) AS latitude,
    ST_AsGeoJSON(impact_polygon_geom)::json AS impact FROM incident_clusters c'''


def golden_from_row(row):
    values = row['canonical_attributes']
    if isinstance(values, str):
        values = json.loads(values)
    if not values:
        values = {name: row[name] for name in ('incident_type','severity','temporal_status',
                  'lanes_blocked','total_lanes','direction','summary_reason')}
        values.update(report_id=str(row['source_report_ids'][0]), landmark_tuple={name: row[name]
            for name in ('corridor_name','primary_landmark','locality','city','state')})
    values = {**values, 'cluster_id': row['cluster_id'], 'report_count': row['report_count'],
        'source_report_ids': row['source_report_ids'], 'attribute_completeness_score': row['attribute_completeness_score'],
        'first_reported_at': row['first_reported_at'], 'last_updated_at': row['last_updated_at'],
        'predicted_clear_at': row['predicted_clear_at'], 'is_active': row['is_active'],
        'last_reported_at': row.get('last_reported_at'),
        'estimated_clear_at': row.get('estimated_clear_at'),
        'expected_clearance_minutes': row.get('expected_clearance_minutes'),
        'clearance_upper_bound_minutes': row.get('clearance_upper_bound_minutes'),
        'waterfall_breakdown': row.get('waterfall_breakdown'),
        'clearance': row.get('waterfall_breakdown'),
        'source_scope': row['source_scope'], 'impact_polygon_geojson': row['impact'],
        'location': {'longitude': row['longitude'], 'latitude': row['latitude'], 'method': 'provided',
                     'confidence': row['geocode_confidence'] or 0,
                     'display_name': row['primary_landmark'] or row['corridor_name']}}
    return GoldenRecordSchema.model_validate(values)


async def read_golden(session, cluster_id):
    row = (await session.execute(text(SELECT_RECORD+' WHERE cluster_id=:id'),
                                  {'id': cluster_id})).mappings().one()
    return golden_from_row(row)


async def list_active(session, limit=100, include_fixtures=True):
    rows = (await session.execute(text(SELECT_RECORD+'''
        WHERE is_active AND cardinality(source_report_ids)>0
            AND (:fixtures OR source_scope='live') ORDER BY last_updated_at DESC,cluster_id LIMIT :limit
    '''), {'fixtures': include_fixtures, 'limit': limit})).mappings().all()
    return [golden_from_row(row) for row in rows]
