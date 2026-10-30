// @file: frontend/src/Inspector.tsx
// @exports: Inspector
// @depends_on: types.ts, api.ts
// @purpose: Canonical source evidence and full three-level clearance/PCU uncertainty inspection.
import {localTime, minute} from './api';
import type {GoldenRecord} from './types';
export default function Inspector({record, close, history}: {record: GoldenRecord; close: () => void;
  history: (corridor: string) => void}) {
  const waterfall = record.waterfall_breakdown || record.clearance;
  return <section className="record-inspector" aria-label="Golden Record inspector">
    <div className="panel-head"><div><small>CANONICAL INTELLIGENCE</small><h2>Golden Record</h2></div>
      <button className="icon-button" onClick={close} aria-label="Close inspector">×</button></div>
    <span className={`badge ${record.source_scope === 'fixture' ? 'amber' : 'green'}`}>{record.source_scope} source</span>
    <h3>{record.landmark_tuple.primary_landmark || 'Unstated landmark'}</h3>
    <p>{record.landmark_tuple.corridor_name} · {record.landmark_tuple.city} · {record.landmark_tuple.state}</p>
    <div className="fact-grid"><div><small>CATEGORY</small><strong>{record.incident_type}</strong></div>
      <div><small>SEVERITY</small><strong>{record.severity}</strong></div>
      <div><small>SOURCE REPORTS</small><strong>{record.report_count}</strong></div>
      <div><small>COMPLETENESS</small><strong>{(record.attribute_completeness_score * 100).toFixed(0)}%</strong></div></div>
    <div className="evidence"><small>LANE EVIDENCE</small><p>{record.lanes_blocked === null ?
      'lanes_blocked: null (Abstained - Unstated in Source)' : `${record.lanes_blocked} lanes explicitly blocked`}</p>
      <span className="badge">{record.lanes_blocked === null ? 'ABSTAINED' : 'EXPLICIT SOURCE EVIDENCE'}</span>
      <p>Total lanes: {record.total_lanes ?? 'Unstated'} · Direction: {record.direction ?? 'Unstated'}</p></div>
    <p>{record.summary_reason}</p>
    <dl><dt>First reported</dt><dd>{localTime(record.first_reported_at)} IST</dd>
      <dt>Updated</dt><dd>{localTime(record.last_updated_at)} IST</dd>
      <dt>Geocode</dt><dd>{record.location.method} · {(record.location.confidence * 100).toFixed(0)}%</dd></dl>
    <h3>Clearance waterfall <span className="badge">IRC-106 PCU</span></h3>
    {waterfall ? <><div className="waterfall">
      {[['01', 'Empirical corridor', waterfall.empirical_minutes, `n = ${waterfall.n1}`],
        ['02', 'Neighbor KNN', waterfall.neighbor_minutes, `n = ${waterfall.n2}`],
        ['03', 'Physical PCU prior', waterfall.physical_minutes, waterfall.lane_input_mode]].map(([level, label, value, note], index) =>
        <div className={`waterfall-step ${waterfall.waterfall_level_used === index + 1 ? 'selected' : ''}`} key={String(level)}>
          <b>{level}</b><div><strong>{label}</strong><small>{note}</small></div><em>{minute(value as number | null)}</em></div>)}
      </div><div className="fact-grid"><div><small>EXPECTED</small><strong>{minute(waterfall.estimated_clearance_mins)}</strong></div>
        <div><small>P90 UPPER ESTIMATE</small><strong>{minute(waterfall.bayesian_bound_mins)}</strong></div>
        <div><small>HARD CEILING</small><strong>{minute(waterfall.hard_ceiling_mins)}</strong></div>
        <div><small>TYPE FLOOR</small><strong>{minute(waterfall.floor_minutes)}</strong></div></div>
      <div className="bayesian-bound" aria-label="Expected and Bayesian upper clearance against hard ceiling">
        <i style={{width: `${waterfall.estimated_clearance_mins / waterfall.hard_ceiling_mins * 100}%`}}/>
        <b style={{left: `${waterfall.bayesian_bound_mins / waterfall.hard_ceiling_mins * 100}%`}}>p90</b></div>
      <small>EXPECTED FILL / P90 TICK / HARD CEILING AT RIGHT EDGE</small>
      <dl><dt>Queue load</dt><dd>{waterfall.pcu_queue_load?.toFixed(1) ?? '—'} PCU</dd>
        <dt>Discharge capacity</dt><dd>{waterfall.discharge_capacity_pcu_hr?.toFixed(1) ?? '—'} PCU/hr</dd>
        <dt>Expected clear</dt><dd>{localTime(record.estimated_clear_at)}</dd>
        <dt>Upper deadline</dt><dd>{localTime(record.predicted_clear_at)}</dd></dl>
      <small>SHRINKAGE WEIGHTS</small><pre>{JSON.stringify(waterfall.weights, null, 2)}</pre>
      <p className="muted">{waterfall.provenance}</p>
      <details><summary>Physical assumptions & component provenance</summary>
        <pre>{JSON.stringify({physical_assumptions: waterfall.physical_assumptions,
          component_provenance: waterfall.component_provenance, uncertainty_method: waterfall.uncertainty_method}, null, 2)}</pre></details>
      <p className="muted">Predictive p90 approximation; field calibration remains pending.</p></> :
      <p className="empty">Awaiting analytics. Run the Analytics stage to estimate clearance.</p>}
    <button className="wide" disabled={!record.landmark_tuple.corridor_name}
      onClick={() => history(record.landmark_tuple.corridor_name!)}>Inspect corridor history ↗</button>
    <details><summary>Canonical record & source IDs</summary><pre>{JSON.stringify(record, null, 2)}</pre></details>
  </section>;
}
