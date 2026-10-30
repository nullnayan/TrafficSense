# @file: backend/analytics/history.py
# @exports: clearance_samples
# @depends_on: sql/06_analytics_pipeline.sql, backend/analytics/pcu_model.py
# @purpose: Metric, scope-aware empirical history and inverse-distance neighbor/choke transfer.
import re

from sqlalchemy import text

from backend.analytics.pcu_model import type_key


async def clearance_samples(session, record, physical_minutes, include_seed=False):
    kind = type_key(record.incident_type)
    legacy = {'accident': 'collision', 'construction': 'roadwork'}.get(kind, kind)
    params = {'lon': record.location.longitude, 'lat': record.location.latitude,
              'kind': kind, 'legacy': legacy, 'scope': record.source_scope, 'seed': include_seed,
              'road': re.sub('[^a-z0-9]', '', (record.landmark_tuple.corridor_name or '').lower())}
    point = 'ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography'
    match = f'''((:road<>'' AND regexp_replace(lower(corridor_name),'[^a-z0-9]','','g')=:road)
                OR ST_DWithin(geom::geography,{point},1000))'''
    scope = "(source_scope=:scope OR (:seed AND source_scope='fixture'))"
    empirical = (await session.execute(text(f'''
        SELECT history_id AS id, observed_clearance_mins AS minutes, source_scope,
            ST_Distance(geom::geography,{point}) AS distance FROM corridor_history
        WHERE incident_type IN (:kind,:legacy) AND {scope} AND {match}
        ORDER BY history_id
    '''), params)).mappings().all()
    neighbors = (await session.execute(text(f'''
        SELECT history_id AS id, observed_clearance_mins AS minutes, source_scope,
            ST_Distance(geom::geography,{point}) AS distance FROM corridor_history
        WHERE incident_type IN (:kind,:legacy) AND {scope} AND NOT {match}
            AND ST_DWithin(geom::geography,{point},15000)
        ORDER BY distance,history_id LIMIT 8
    '''), params)).mappings().all()
    chokes = (await session.execute(text(f'''
        SELECT choke_id AS id, base_delay_minutes, peak_multiplier, choke_radius_meters,
            source_scope, ST_Distance(geom::geography,{point}) AS distance
        FROM corridor_choke_points WHERE {scope}
            AND ST_DWithin(geom::geography,{point},15000)
        ORDER BY distance,choke_id LIMIT 8
    '''), params)).mappings().all()
    samples = [{**row, 'weight': 1 / max(100, row['distance']), 'source': 'history'} for row in neighbors]
    for row in chokes:
        peak = row['distance'] <= row['choke_radius_meters']
        delay = row['base_delay_minutes'] * (row['peak_multiplier'] if peak else 1)
        samples.append({**row, 'minutes': physical_minutes + delay,
                        'weight': 1 / max(100, row['distance']), 'source': 'choke_prior',
                        'peak_applied': peak})
    return [dict(row) for row in empirical], sorted(samples, key=lambda row: (row['distance'], str(row['id'])))[:8]
