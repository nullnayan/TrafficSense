# @file: tests/test_scout_journey.py
# @exports: Sequential delay propagation regression tests
# @depends_on: backend/scout/journey.py, tests/phase6_support.py, tests/test_scout.py
# @purpose: Prevent double-counted downstream clearance waits and disclose blockage separately from driving.
from datetime import timedelta

import pytest

from backend.scout.journey import plan_journey
from backend.scout.topology import rank_chokes, sample_chokes
from tests.phase6_support import NOW, golden
from tests.test_scout import route


def test_upstream_hold_clears_downstream_incident():
    geometry = route()
    first, second = golden(120, 150, longitude=77.01), golden(30, 40, longitude=77.5)
    hazards, _, metrics = plan_journey([second, first], geometry, [], NOW)
    assert hazards[0].overlap == 'BLOCKED_AT_ETA'
    assert hazards[1].overlap == 'CLEARED_BEFORE_ETA' and hazards[1].delay_minutes == 0
    assert metrics['blocked_wait_minutes'] == pytest.approx(hazards[0].delay_minutes)
    assert metrics['journey_status'] == 'departure_hold'
    assert hazards[1].arrival_at == NOW + timedelta(minutes=hazards[1].travel_minutes_to_incident)
    assert metrics['modeled_choke_delay_minutes'] == metrics['residual_delay_minutes'] == 0


def test_choke_flags_follow_delay_adjusted_hazard_verdict():
    geometry = route()
    records = [golden(120, 150, longitude=77.01), golden(30, 40, longitude=77.5)]
    row = {'choke_id': records[1].cluster_id, 'choke_name': 'Downstream', 'longitude': 77.5,
           'latitude': 30, 'fraction': .5, 'base_delay_minutes': 5, 'peak_multiplier': 1}
    chokes = sample_chokes(rank_chokes([row], []), [], geometry)
    _, normalized, _ = plan_journey(records, geometry, chokes, NOW)
    assert normalized[0].overlaps_incident is False
    assert normalized[0].predicted_clearance_minutes == 0
    assert normalized[0].risk_score == 5
