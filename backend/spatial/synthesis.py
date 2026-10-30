# @file: backend/spatial/synthesis.py
# @exports: synthesize, upsert_golden
# @depends_on: backend/schemas/extraction.py, sql/05_spatial_pipeline.sql
# @purpose: Credible non-null canonical attributes, lane abstention, spatial aggregates and stable revision upserts.

from sqlalchemy import text

from backend.schemas.extraction import ExtractedIncidentSchema
from backend.schemas.ingestion import utc_now


def synthesize(rows):
    def rank(row):
        attributes = row['extracted_attributes']
        completeness = sum(value not in (None, '', 'unknown') for value in attributes.values())
        return (attributes.get('extraction_confidence') if attributes.get('extraction_confidence') is not None
                else row['setfit_confidence'] if row['setfit_confidence'] is not None else 0.5,
                row['geocode_confidence'] or 0, completeness, row['published_at'], str(row['report_id']))
    ordered = sorted(rows, key=rank, reverse=True)
    credible = [row for row in ordered if row['extracted_attributes'].get('extraction_confidence') is None
                or row['extracted_attributes']['extraction_confidence'] >= 0.5]
    canonical = dict(ordered[0]['extracted_attributes'])
    canonical['report_id'] = str(ordered[0]['report_id'])
    for name in ('severity','direction','summary_reason','occurred_at','lanes_blocked','total_lanes'):
        sources = credible if name in ('lanes_blocked','total_lanes') else ordered
        canonical[name] = next((row['extracted_attributes'].get(name) for row in sources
                               if row['extracted_attributes'].get(name) not in (None, '', 'unknown')),
                              'unknown' if name == 'severity' else None)
    landmark = dict(canonical['landmark_tuple'])
    for name in ('primary_landmark','corridor_name','locality','city','state'):
        landmark[name] = next((row['extracted_attributes']['landmark_tuple'].get(name) for row in ordered
                              if row['extracted_attributes']['landmark_tuple'].get(name)), None)
    canonical['landmark_tuple'] = landmark
    blocked = canonical['lanes_blocked']
    if blocked is not None and canonical['total_lanes'] is not None and blocked > canonical['total_lanes']:
        canonical['total_lanes'] = next((row['extracted_attributes'].get('total_lanes') for row in credible
            if row['extracted_attributes'].get('total_lanes') is not None
            and row['extracted_attributes']['total_lanes'] >= blocked), None)
    result = ExtractedIncidentSchema.model_validate(canonical)
    fields = [*landmark.values(), result.lanes_blocked, result.total_lanes, result.direction,
              str(result.severity), str(result.incident_type), result.summary_reason]
    # country_code is fixed metadata, not incident completeness evidence.
    fields.remove('IN')
    score = sum(value not in (None, '', 'unknown') for value in fields)/len(fields)
    return result, score


async def upsert_golden(session, cluster_id, rows, canonical, score):
    ids = sorted({row['report_id'] for row in rows}, key=str)
    values = canonical.model_dump(mode='json')
    values.update(canonical.landmark_tuple.model_dump())
    values.update(id=cluster_id, ids=ids, count=len(ids), score=score,
        first=min(row['published_at'] for row in rows), last=max(row['published_at'] for row in rows),
        now=max(utc_now(), max(row['published_at'] for row in rows)),
        attributes=canonical.model_dump_json(), scope='fixture' if rows[0]['source_name'].startswith('fixture:') else 'live',
        confidence=sum(row['geocode_confidence'] or 0 for row in rows)/len(rows))
    return (await session.execute(text('''
        WITH shape AS (SELECT ST_Centroid(ST_Collect(geom)) AS center FROM raw_reports
            WHERE report_id=ANY(CAST(:ids AS uuid[])))
        INSERT INTO incident_clusters (cluster_id,incident_type,severity,temporal_status,corridor_name,
            primary_landmark,locality,city,state,lanes_blocked,total_lanes,direction,summary_reason,
            report_count,source_report_ids,attribute_completeness_score,first_reported_at,last_reported_at,
            last_updated_at,is_active,centroid_geom,impact_polygon_geom,canonical_attributes,source_scope,geocode_confidence)
        SELECT :id,:incident_type,:severity,'active',:corridor_name,:primary_landmark,:locality,:city,:state,
            :lanes_blocked,:total_lanes,:direction,:summary_reason,:count,CAST(:ids AS uuid[]),:score,:first,:last,
            :now,true,center,ST_Buffer(center::geography,:eps)::geometry,CAST(:attributes AS jsonb),:scope,:confidence
        FROM shape WHERE true ON CONFLICT (cluster_id) DO UPDATE SET
            incident_type=EXCLUDED.incident_type,severity=EXCLUDED.severity,corridor_name=EXCLUDED.corridor_name,
            primary_landmark=EXCLUDED.primary_landmark,locality=EXCLUDED.locality,city=EXCLUDED.city,state=EXCLUDED.state,
            lanes_blocked=EXCLUDED.lanes_blocked,total_lanes=EXCLUDED.total_lanes,direction=EXCLUDED.direction,
            summary_reason=EXCLUDED.summary_reason,report_count=EXCLUDED.report_count,
            source_report_ids=EXCLUDED.source_report_ids,attribute_completeness_score=EXCLUDED.attribute_completeness_score,
            first_reported_at=EXCLUDED.first_reported_at,last_reported_at=EXCLUDED.last_reported_at,
            last_updated_at=EXCLUDED.last_updated_at,centroid_geom=EXCLUDED.centroid_geom,
            impact_polygon_geom=EXCLUDED.impact_polygon_geom,canonical_attributes=EXCLUDED.canonical_attributes,
            is_active=true,temporal_status='active',predicted_clear_at=NULL,
            estimated_clearance_mins=NULL,bayesian_bound_mins=NULL,waterfall_level_used=NULL,
            pcu_queue_load=NULL,discharge_capacity_pcu_hr=NULL,hard_ceiling_mins=NULL,
            geocode_confidence=EXCLUDED.geocode_confidence,cluster_revision=incident_clusters.cluster_revision+1
        RETURNING cluster_revision
    '''), {**values, 'eps': 500.0})).scalar_one()
