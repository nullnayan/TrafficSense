# @file: backend/scout/journey.py
# @exports: plan_journey
# @depends_on: backend/scout/evaluator.py, backend/scout/geometry.py
# @purpose: Propagate upstream waits into downstream arrivals and separate driving delay from blockage holds.
from datetime import timedelta

from backend.scout.evaluator import evaluate_overlap
from backend.scout.geometry import distance


def plan_journey(records, route, chokes, departure_at):
    events = [(route.project([r.location.longitude, r.location.latitude])[0], 0, r) for r in records]
    events += [(route.project([c.location.longitude, c.location.latitude])[0], 1, c) for c in chokes]
    events.sort(key=lambda event: (event[0], event[1]))
    delay = blocked = residual = choke_delay = 0.0
    hazards, updated = [], {}
    for _, kind, item in events:
        if kind == 1:
            updated[item.choke_id] = item.model_copy(update={'arrival_minutes': item.arrival_minutes + delay})
            choke_delay += item.delay_minutes
            delay += item.delay_minutes
        else:
            hazard = evaluate_overlap(item, route, departure_at + timedelta(minutes=delay))
            hazards.append(hazard.model_copy(update={'travel_minutes_to_incident':
                (hazard.arrival_at - departure_at).total_seconds() / 60}))
            if hazard.overlap == 'BLOCKED_AT_ETA':
                blocked += hazard.delay_minutes
            else:
                residual += hazard.delay_minutes
            delay += hazard.delay_minutes
    normalized = []
    for choke in chokes:
        current = updated[choke.choke_id]
        nearby = [h for h in hazards if distance([current.location.longitude, current.location.latitude],
            [h.golden_record['location']['longitude'], h.golden_record['location']['latitude']]) <= 500]
        normalized.append(current.model_copy(update={
            'overlaps_incident': any(h.overlap != 'CLEARED_BEFORE_ETA' for h in nearby),
            'predicted_clearance_minutes': max((h.delay_minutes for h in nearby), default=None),
            'risk_score': current.delay_minutes + sum(h.delay_minutes for h in nearby)}))
    return hazards, normalized, {
        'modeled_choke_delay_minutes': choke_delay, 'blocked_wait_minutes': blocked,
        'residual_delay_minutes': residual, 'journey_status': 'departure_hold' if blocked > 0 else 'open'}
