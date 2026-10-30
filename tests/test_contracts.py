# @file: tests/test_contracts.py
# @exports: Contract regression tests
# @depends_on: backend/schemas, backend/core/config.py
# @purpose: Validate abstention, bounds, provenance, timestamps, and safe query slots.
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.core.config import Settings
from backend.schemas import (
    ExtractedIncidentSchema,
    GeocodedLocation,
    GoldenRecordSchema,
    ParameterizedSQLResponse,
    RawIngestedItem,
    SlotFillingQuery,
    WaterfallBreakdown,
)


def test_unknown_lanes_abstain_and_json_roundtrip():
    incident = ExtractedIncidentSchema(report_id=uuid4())
    assert incident.lanes_blocked is None
    assert ExtractedIncidentSchema.model_validate_json(incident.model_dump_json()) == incident
    assert incident.model_json_schema()['additionalProperties'] is False


@pytest.mark.parametrize('values', [
    {'lanes_blocked': -1}, {'lanes_blocked': True}, {'lanes_blocked': 1.5},
    {'lanes_blocked': 3, 'total_lanes': 2}, {'severity': 'invented'},
    {'extraction_confidence': 1.1}, {'extraction_confidence': float('nan')},
    {'occurred_at': '2026-01-01T10:00:00'}, {'fabricated': 'value'},
    {'landmark_tuple': {'guessed_latitude': 30}},
])
def test_invalid_extractions_fail(values):
    with pytest.raises(ValidationError):
        ExtractedIncidentSchema(report_id=uuid4(), **values)


def test_ingestion_requires_aware_publication():
    values = dict(source_name='rss', source_url='https://example.org/1', title='Closure',
                  raw_text='One lane closed', published_at='2026-01-01T10:00:00Z')
    assert RawIngestedItem(**values).ingested_at.tzinfo is not None
    with pytest.raises(ValidationError):
        RawIngestedItem(**{**values, 'published_at': '2026-01-01T10:00:00'})


@pytest.mark.parametrize('values', [
    {'latitude': 91, 'longitude': 78}, {'latitude': 30, 'longitude': 181},
    {'latitude': 30, 'longitude': 78, 'method': 'unresolved'},
])
def test_spatial_bounds(values):
    with pytest.raises(ValidationError):
        GeocodedLocation(**{'method': 'provided', 'confidence': 1, **values})


def test_clearance_bounds_and_provenance():
    values = dict(waterfall_level_used=3, estimated_clearance_mins=40,
                  bayesian_bound_mins=60, hard_ceiling_mins=90, provenance='queue_model')
    assert WaterfallBreakdown(**values).estimated_clearance_mins == 40
    with pytest.raises(ValidationError):
        WaterfallBreakdown(**{**values, 'estimated_clearance_mins': 70})


@pytest.mark.parametrize('values', [
    {'intent': 'drop_table', 'slots': {}},
    {'intent': 'nearby_incidents', 'slots': {}},
    {'intent': 'corridor_history', 'slots': {}},
    {'intent': 'congestion_hotspots', 'slots': {'latitude': 30}},
    {'intent': 'average_clearance', 'slots': {'limit': 1000}},
    {'intent': 'average_clearance', 'slots': {'sql': 'DROP TABLE raw_reports'}},
])
def test_query_rejections(values):
    with pytest.raises(ValidationError):
        SlotFillingQuery(**values)


def test_query_text_remains_a_bound_value():
    query = SlotFillingQuery(intent='corridor_history', slots={'corridor_name': "'; DROP TABLE x;--"})
    response = ParameterizedSQLResponse(template_id=query.intent, parameters=query.slots,
                                        explanation='Use a server-owned bound template')
    assert response.parameters.corridor_name == query.slots.corridor_name
    with pytest.raises(ValidationError):
        ParameterizedSQLResponse(**response.model_dump(), sql='SELECT 1')


def test_golden_record_source_consistency():
    report_id = uuid4()
    values = dict(report_id=report_id, cluster_id=uuid4(), report_count=1,
                  source_report_ids=[report_id], attribute_completeness_score=0.5,
                  location={'latitude': 30, 'longitude': 78, 'method': 'provided', 'confidence': 1},
                  first_reported_at='2026-01-01T10:00:00Z')
    assert GoldenRecordSchema(**values).report_count == 1
    with pytest.raises(ValidationError):
        GoldenRecordSchema(**{**values, 'source_report_ids': [uuid4()]})


def test_settings_load_example_and_validate_driver():
    settings = Settings(_env_file='.env.example')
    assert settings.dbscan_eps_meters == 500
    assert settings.macro_sentry_poll_hours == 2
    assert 'trafficsense_dev' not in repr(settings)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url='postgresql://localhost/test')
