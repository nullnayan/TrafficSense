// @file: frontend/src/eta.ts
// @exports: journeyTiming
// @depends_on: types.ts
// @purpose: Keep baseline driving, modeled traffic delay and clearance waiting distinct and honest.
import type {Choke, ScoutResult} from './types';
export function chokeHazard(choke: Choke, route: ScoutResult) {
  const nearby = route.incidents.filter(i => Math.hypot((i.golden_record.location.longitude - choke.location.longitude) * 96000,
    (i.golden_record.location.latitude - choke.location.latitude) * 111000) <= 500);
  return nearby.find(i => i.overlap === 'BLOCKED_AT_ETA') || nearby.find(i => i.overlap === 'RESIDUAL_RISK') || nearby[0];
}
export function journeyTiming(route: ScoutResult) {
  const base = route.duration_seconds / 60;
  const wait = route.blocked_wait_minutes ?? route.incidents.filter(i => i.overlap === 'BLOCKED_AT_ETA').reduce((n, i) => n + i.delay_minutes, 0);
  const local = route.modeled_choke_delay_minutes ?? route.choke_points.reduce((n, c) => n + c.delay_minutes, 0);
  const residual = route.residual_delay_minutes ?? route.incidents.filter(i => i.overlap === 'RESIDUAL_RISK').reduce((n, i) => n + i.delay_minutes, 0);
  return {base, wait, delay: local + residual, drive: base + local + residual,
    elapsed: route.adjusted_duration_seconds / 60, hold: wait > 0};
}
