# @file: tests/test_analytics.py
# @exports: PCU, shrinkage, selection, hard-bound and synthetic benchmark tests
# @depends_on: backend/analytics/{pcu_model,waterfall,benchmark}.py, tests/analytics_support.py
# @purpose: Verify numerical invariants without fabricating lane evidence.
import math

import pytest

from backend.analytics.benchmark import benchmark
from backend.analytics.pcu_model import TYPE_BOUNDS, pcu_load, physical_queue
from backend.analytics.waterfall import estimate_clearance
from tests.analytics_support import golden


@pytest.mark.parametrize('road,state,baseline', [('NH-58', None, 1500), ('SH-2', None, 1500),
    ('Delhi Ring Road', None, 1200), ('NH-7', 'Uttarakhand', 900), ('Mussoorie Road', None, 900)])
def test_capacity_priors_and_abstention(road, state, baseline):
    record = golden(landmark_tuple={'corridor_name': road, 'state': state})
    before = record.model_dump()
    result = physical_queue(record)
    assert result['physical_assumptions']['lane_capacity_pcu_hr'] == baseline
    assert result['lane_input_mode'] == 'abstained_severity_prior'
    assert record.lanes_blocked is None and record.total_lanes is None
    assert record.model_dump() == before


def test_explicit_ratio_and_deterministic_queue_units():
    record = golden(lanes_blocked=1, total_lanes=2)
    result = physical_queue(record)
    assert result['effective_capacity_drop_ratio'] == 0.5
    assert result['discharge_capacity_pcu_hr'] == 1500
    assert result['pcu_queue_load'] == 500
    assert result['physical_minutes'] == 80
    assert result['lane_input_mode'] == 'explicit_ratio'
    assert physical_queue(golden(lanes_blocked=1))['lane_input_mode'] == 'explicit_lanes_default_corridor'
    assert pcu_load({'car': 4, 'truck': 2, 'two_wheeler': 2}) == 11
    with pytest.raises(ValueError):
        pcu_load({'car': math.inf})


@pytest.mark.parametrize('n1,n2,level', [(0, 0, 3), (2, 0, 3), (0, 1, 2), (2, 1, 2), (3, 0, 1)])
def test_selection_and_shrinkage(n1, n2, level):
    result = estimate_clearance(golden(), [{'minutes': 100}] * n1,
                                [{'minutes': 80, 'weight': 1}] * n2)
    assert result.selected_level == level
    assert result.weights['empirical'] == pytest.approx(n1 / (n1 + 3))
    assert sum(result.weights.values()) == pytest.approx(1)
    expected = (n1 / (n1 + 3) * 100 + (1 - n1 / (n1 + 3)) *
                (n2 / (n2 + 3) * 80 + (1 - n2 / (n2 + 3)) * 48))
    assert result.estimated_clearance_mins == pytest.approx(expected)
    assert result.upper_bound_minutes >= result.estimated_clearance_mins


def test_smooth_transition_and_distance_weighting():
    results = [estimate_clearance(golden(), [{'minutes': 100}] * n).estimated_clearance_mins
               for n in range(5)]
    assert all(0 < b - a <= 15 for a, b in zip(results, results[1:]))
    result = estimate_clearance(golden(), neighbors=[{'minutes': 100, 'weight': 0.01},
                                                   {'minutes': 20, 'weight': 0.001}])
    assert result.neighbor_minutes == pytest.approx(92.7272727)


@pytest.mark.parametrize('kind,bounds', TYPE_BOUNDS.items())
def test_strict_type_floors_and_ceilings(kind, bounds):
    low = estimate_clearance(golden(incident_type=kind), [{'minutes': 0}] * 10000)
    high = estimate_clearance(golden(incident_type=kind, severity='catastrophic'),
                             [{'minutes': 100000}] * 4)
    assert low.estimated_clearance_mins == bounds[0]
    assert high.estimated_clearance_mins == high.upper_bound_minutes == bounds[1]


def test_extreme_hill_landslide_and_invalid_samples():
    record = golden(incident_type='landslide', severity='catastrophic', lanes_blocked=2, total_lanes=2,
                    landmark_tuple={'corridor_name': 'Rishikesh-Badrinath NH-7'})
    result = estimate_clearance(record)
    assert result.physical_minutes > 720
    assert result.estimated_clearance_mins == result.upper_bound_minutes == 720
    for value in (math.inf, -1, math.nan):
        with pytest.raises(ValueError):
            estimate_clearance(record, [{'minutes': value}])


def test_honestly_labeled_benchmark():
    result = benchmark()
    assert result['sample_count'] == 24 and result['ceiling_compliance'] == 1
    assert result['level_coverage'] == {'1': 8, '2': 8, '3': 8}
    assert 'synthetic' in result['dataset'] and 'pending' in result['p90_status']
