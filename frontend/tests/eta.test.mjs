// @file: frontend/tests/eta.test.mjs
// @exports: ETA display and duration boundary regressions
// @depends_on: node:test, src/api.ts, src/eta.ts
// @purpose: Prevent closure waits from masquerading as driving and eliminate 60-minute clock formatting.
import test from 'node:test';
import assert from 'node:assert/strict';
import {duration} from '../src/api.ts';
import {journeyTiming} from '../src/eta.ts';
test('closure hold stays separate from modeled driving', () => {
  const route = {duration_seconds: 54 * 60, adjusted_duration_seconds: 673 * 60,
    blocked_wait_minutes: 600, modeled_choke_delay_minutes: 19, residual_delay_minutes: 0,
    incidents: [], choke_points: []};
  assert.deepEqual(journeyTiming(route), {base: 54, wait: 600, delay: 19, drive: 73, elapsed: 673, hold: true});
});
test('legacy responses disclose blockage and residual components', () => {
  const route = {duration_seconds: 30 * 60, adjusted_duration_seconds: 59 * 60,
    incidents: [{overlap: 'BLOCKED_AT_ETA', delay_minutes: 20}, {overlap: 'RESIDUAL_RISK', delay_minutes: 4}],
    choke_points: [{delay_minutes: 5}]};
  assert.equal(journeyTiming(route).drive, 39);
  assert.equal(journeyTiming(route).wait, 20);
});
test('rounding carries minutes into the next hour', () => {
  assert.equal(duration(719.8), '12h 0m');
  assert.equal(duration(119.6), '2h 0m');
  assert.equal(duration(54.2), '54 min');
});
