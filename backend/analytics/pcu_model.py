# @file: backend/analytics/pcu_model.py
# @exports: physical_queue, type_key, TYPE_BOUNDS, pcu_load
# @depends_on: backend/schemas/spatial.py
# @purpose: Assumption-labeled Indian capacity priors and deterministic demand/capacity queue math.
import math
import re

TYPE_BOUNDS = {'accident': (15, 240), 'breakdown': (10, 120), 'congestion': (15, 180),
               'waterlogging': (30, 360), 'landslide': (60, 720), 'construction': (45, 480),
               'protest': (30, 360), 'other': (15, 240)}
ALIASES = {'collision': 'accident', 'roadwork': 'construction', 'road_closure': 'other',
           'bridge_damage': 'other', 'unknown': 'other'}
BASE_SERVICE = {'accident': 40, 'breakdown': 20, 'congestion': 25, 'waterlogging': 90,
                'landslide': 180, 'construction': 120, 'protest': 80, 'other': 45}
PCU_FACTORS = {'car': 1.0, 'two_wheeler': 0.5, 'auto_rickshaw': 1.2, 'bus': 3.0,
               'truck': 3.0, 'bicycle': 0.5}


def type_key(incident_type):
    key = str(incident_type).lower()
    return ALIASES.get(key, key) if key in TYPE_BOUNDS or key in ALIASES else 'other'


def pcu_load(vehicle_counts):
    """Optional composition input; fixed equivalents are priors, not measured mix."""
    if any(kind not in PCU_FACTORS or not math.isfinite(count) or count < 0
           for kind, count in vehicle_counts.items()):
        raise ValueError('Vehicle counts must be finite, nonnegative and of known classes')
    return sum(PCU_FACTORS[kind] * count for kind, count in vehicle_counts.items())


def physical_queue(record, weather_condition=None):
    landmark = record.landmark_tuple
    road = (landmark.corridor_name or '').lower()
    region = ' '.join(str(value or '') for value in
                      (road, landmark.primary_landmark, landmark.locality, landmark.city, landmark.state)).lower()
    hill = any(word in region for word in ('ghat', 'hill', 'mountain', 'badrinath', 'mussoorie',
                                          'tehri', 'kedarnath', 'gangotri', 'chamoli', 'rudraprayag'))
    hill = hill or ('uttarakhand' in region and bool(re.search(r'nh[\s-]*7\b', road)))
    highway = bool(re.search(r'\b(?:nh|sh)\b|\b(?:nh|sh)[\s-]*\d|expressway|national highway|state highway', road))
    baseline = 900 if hill else 1500 if highway else 1200
    corridor_lanes = 2 if hill or highway else 4
    lanes = record.total_lanes if record.total_lanes is not None else corridor_lanes
    severity = {'minor': 0.7, 'moderate': 1.0, 'severe': 1.6, 'catastrophic': 2.5}.get(
        str(record.severity), 1.0)
    kind = type_key(record.incident_type)
    if record.lanes_blocked is None:
        mode = 'abstained_severity_prior'
        type_prior = {'landslide': 0.65, 'waterlogging': 0.5, 'construction': 0.35,
                      'protest': 0.5, 'congestion': 0.2}.get(kind, 0.3)
        drop = type_prior * severity
    else:
        mode = 'explicit_ratio' if record.total_lanes is not None else 'explicit_lanes_default_corridor'
        drop = record.lanes_blocked / lanes
    drop = min(0.95, max(0.10, drop))
    weather = (weather_condition or record.summary_reason or '').lower()
    weather_factor = 1.25 if any(word in weather for word in ('rain', 'flood', 'snow', 'storm')) else 1.0
    terrain_factor = 1.3 if hill else 1.0
    # A corroboration prior affects service burden; it is not an observed traffic count.
    corroboration = 1 + 0.08 * math.log1p(min(record.report_count - 1, 100))
    service = BASE_SERVICE[kind] * severity * terrain_factor * weather_factor * corroboration
    capacity = baseline * lanes / (terrain_factor * weather_factor)
    demand = 0.75 * capacity
    reduced = capacity * (1 - drop)
    queue = max(0.0, demand - reduced) * service / 60
    # Capacity is restored after service; dissipation uses net capacity minus arrivals.
    dissipation = queue / (capacity - demand) * 60
    return {'physical_minutes': service + dissipation, 'pcu_queue_load': queue,
            'discharge_capacity_pcu_hr': reduced, 'lane_input_mode': mode,
            'effective_capacity_drop_ratio': drop,
            'physical_assumptions': {'lane_capacity_pcu_hr': baseline, 'internal_total_lanes': lanes,
                'corridor_default_lanes': corridor_lanes, 'demand_ratio': 0.75,
                'restored_capacity_pcu_hr': capacity, 'demand_pcu_hr': demand,
                'service_minutes': service, 'dissipation_minutes': dissipation,
                'weather_factor': weather_factor, 'hill_factor': terrain_factor,
                'report_count_factor': corroboration,
                'capacity_source': 'Phase 5 supplied priors; IRC-106/HCM-inspired, not a certified formula'}}
