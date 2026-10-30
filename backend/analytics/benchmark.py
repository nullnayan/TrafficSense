# @file: backend/analytics/benchmark.py
# @exports: benchmark, main
# @depends_on: backend/analytics/waterfall.py, backend/core/config.py
# @purpose: Reproducible synthetic reference errors, predictive coverage and ceiling compliance.
import json
import math
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

from backend.analytics.pcu_model import TYPE_BOUNDS
from backend.analytics.waterfall import estimate_clearance
from backend.core.config import ROOT
from backend.schemas.spatial import GoldenRecordSchema

REFERENCES = {'accident': 65, 'breakdown': 30, 'congestion': 40, 'waterlogging': 120,
              'landslide': 240, 'construction': 150, 'protest': 100, 'other': 60}


def benchmark():
    cases = []
    timestamp = datetime(2026, 10, 14, tzinfo=timezone.utc)
    for kind, target in REFERENCES.items():
        for level in (1, 2, 3):
            record = GoldenRecordSchema(report_id=UUID(int=1), cluster_id=UUID(int=2),
                incident_type=kind, severity='moderate', landmark_tuple={'corridor_name': 'NH-58'},
                report_count=1, source_report_ids=[UUID(int=1)], attribute_completeness_score=0.5,
                first_reported_at=timestamp, last_updated_at=timestamp, last_reported_at=timestamp,
                location={'longitude': 77.7, 'latitude': 29.0, 'method': 'provided', 'confidence': 1},
                source_scope='fixture')
            # Independently authored reference durations; artificial histories are not field data.
            empirical = [{'minutes': target * ratio} for ratio in (0.8, 0.9, 1.1, 1.2)] if level == 1 else []
            neighbors = [{'minutes': target * ratio, 'weight': weight}
                         for ratio, weight in ((0.9, 1), (1.2, 0.5))] if level == 2 else []
            result = estimate_clearance(record, empirical, neighbors)
            cases.append({'type': kind, 'reference_minutes': target, 'selected_level': result.selected_level,
                          'expected_minutes': result.estimated_clearance_mins,
                          'upper_bound_minutes': result.upper_bound_minutes,
                          'error_minutes': result.estimated_clearance_mins - target,
                          'ceiling_compliant': TYPE_BOUNDS[kind][0] <= result.estimated_clearance_mins
                          <= result.upper_bound_minutes <= TYPE_BOUNDS[kind][1]})
    errors = [case['error_minutes'] for case in cases]
    return {'dataset': '24 authored synthetic references; eight types x three levels; no field evidence',
            'method': 'in-memory math benchmark; SQL retrieval and worker recovery tested separately',
            'p90_status': 'CV=0.35 moment-matched Bayesian approximation; field calibration pending',
            'sample_count': len(cases), 'mae_minutes': sum(abs(error) for error in errors) / len(errors),
            'rmse_minutes': math.sqrt(sum(error ** 2 for error in errors) / len(errors)),
            'ceiling_compliance': sum(case['ceiling_compliant'] for case in cases) / len(cases),
            'upper_bound_reference_coverage': sum(case['upper_bound_minutes'] >= case['reference_minutes']
                                                for case in cases) / len(cases),
            'level_coverage': dict(Counter(str(case['selected_level']) for case in cases)), 'cases': cases}


def main():
    path = ROOT / 'benchmarks/clearance_waterfall.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(benchmark(), indent=2) + '\n', encoding='utf-8')
    print(path)


if __name__ == '__main__':
    main()
