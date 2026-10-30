# @file: backend/analytics/waterfall.py
# @exports: estimate_clearance, analyze_golden
# @depends_on: backend/analytics/{pcu_model,history}.py, backend/schemas/spatial.py
# @purpose: Three-level sample shrinkage, posterior predictive bounds and strict type clamps.
import math
from datetime import timedelta
from statistics import fmean, variance

from backend.analytics.history import clearance_samples
from backend.analytics.pcu_model import TYPE_BOUNDS, physical_queue, type_key
from backend.schemas.spatial import WaterfallBreakdown


def estimate_clearance(record, empirical=(), neighbors=(), weather_condition=None):
    physical = physical_queue(record, weather_condition)
    mu3 = physical['physical_minutes']
    n1, n2 = len(empirical), len(neighbors)
    values = [float(row['minutes']) for row in empirical]
    neighbor_values = [float(row['minutes']) for row in neighbors]
    weights = [float(row.get('weight', 1)) for row in neighbors]
    if any(not math.isfinite(value) or value < 0 for value in values + neighbor_values):
        raise ValueError('Clearance samples must be finite and nonnegative')
    if any(not math.isfinite(value) or value <= 0 for value in weights):
        raise ValueError('Neighbor weights must be finite and positive')
    mu1 = fmean(values) if n1 else None
    var1 = variance(values) if n1 > 1 else (0.35 * (mu1 or mu3)) ** 2
    mu2 = sum(v * w for v, w in zip(neighbor_values, weights)) / sum(weights) if n2 else None
    var2 = (sum(w * (v - mu2) ** 2 for v, w in zip(neighbor_values, weights)) / sum(weights)
            if n2 > 1 else (0.35 * (mu2 or mu3)) ** 2)
    w1 = n1 / (n1 + 3)
    w2 = (1 - w1) * n2 / (n2 + 3)
    w3 = 1 - w1 - w2
    means = [mu1 if mu1 is not None else mu3, mu2 if mu2 is not None else mu3, mu3]
    ws = [w1, w2, w3]
    mean = sum(w * mu for w, mu in zip(ws, means))
    dispersions = [var1, var2, (0.35 * mu3) ** 2]
    predictive = sum(w * (v + (mu - mean) ** 2) for w, v, mu in zip(ws, dispersions, means))
    # Effective neighbor sample size captures concentration from inverse-distance weighting.
    effective_n2 = sum(weights) ** 2 / sum(w * w for w in weights) if n2 else 0
    uncertainty = sum(w * w * v / (n + 3) for w, v, n in
                      zip(ws, dispersions, [n1, effective_n2, 0]))
    upper = mean + 1.281551565545 * math.sqrt(predictive + uncertainty)
    floor, ceiling = TYPE_BOUNDS[type_key(record.incident_type)]
    expected = min(ceiling, max(floor, mean))
    bound = min(ceiling, max(expected, upper))
    level = 1 if n1 >= 3 else 2 if n2 else 3
    def provenance(rows):
        return [{'id': str(row.get('id', 'reference')), 'scope': row.get('source_scope', 'synthetic_reference'),
                 'source': row.get('source', 'history'), 'distance_m': row.get('distance'),
                 'peak_applied': row.get('peak_applied', False), 'weight': row.get('weight')}
                for row in rows]
    return WaterfallBreakdown(waterfall_level_used=level, selected_level=level,
        estimated_clearance_mins=expected, bayesian_bound_mins=bound, hard_ceiling_mins=ceiling,
        upper_bound_minutes=bound, floor_minutes=floor, empirical_minutes=mu1, neighbor_minutes=mu2,
        n1=n1, n2=n2, empirical_variance=var1 if n1 else 0, neighbor_variance=var2 if n2 else 0,
        weights={'empirical': w1, 'neighbor': w2, 'physical': w3},
        provenance=f'scope={record.source_scope}; empirical n={n1}; neighbor n={n2}; physical supplied priors',
        component_provenance={'empirical': provenance(empirical), 'neighbor': provenance(neighbors),
                              'neighbor_effective_sample_size': effective_n2},
        uncertainty_method='moment-matched posterior predictive p90; CV=0.35, kappa=3; field calibration pending',
        **physical)


async def analyze_golden(session, record, include_seed=False):
    prior = physical_queue(record)
    empirical, neighbors = await clearance_samples(session, record, prior['physical_minutes'], include_seed)
    breakdown = estimate_clearance(record, empirical, neighbors)
    anchor = record.last_reported_at or record.last_updated_at
    clear_at = anchor + timedelta(minutes=breakdown.estimated_clearance_mins)
    return record.model_copy(update={'clearance': breakdown, 'waterfall_breakdown': breakdown,
        'expected_clearance_minutes': breakdown.estimated_clearance_mins,
        'estimated_clear_at': clear_at, 'predicted_clear_at': clear_at})
