// @file: frontend/src/Horizon.tsx
// @exports: Horizon
// @depends_on: types.ts, api.ts, panels.css
// @purpose: Arrival-versus-clearance dual-bar horizon with actual terrain/PCU provenance and provider badges.
import {duration} from './api';
import {chokeHazard} from './eta';
import type {Choke, ScoutResult, TerrainPoint} from './types';
export default function Horizon({choke, route, terrain}: {choke: Choke; route: ScoutResult; terrain?: TerrainPoint}) {
  const hazard = chokeHazard(choke, route);
  const departure = new Date(route.baseline_eta).getTime() - route.duration_seconds * 1000;
  const clear = hazard ? Math.max(0, (new Date(hazard.estimated_clear_at).getTime() - departure) / 60000) : 0;
  const upper = hazard ? Math.max(clear, (new Date(hazard.clear_upper_at).getTime() - departure) / 60000) : 0;
  const scale = Math.max(choke.arrival_minutes + 10, upper + 10, 60);
  const verdict = hazard?.overlap || 'CLEARED_BEFORE_ETA';
  const color = verdict === 'BLOCKED_AT_ETA' ? 'red' : verdict === 'RESIDUAL_RISK' ? 'amber' : 'green';
  return <article className="horizon-card"><div className="horizon-heading"><strong>{choke.choke_name}</strong><b>+{duration(choke.delay_minutes)}</b></div>
    <span className={`status-tag ${color}`}>{hazard ? verdict : 'NO MATCHED INCIDENT'}</span>
    <div className="horizon-axis"><span>ARRIVAL</span><div className="horizon-track"><i className="arrival-marker"
      style={{left: `${Math.min(98, choke.arrival_minutes / scale * 100)}%`}}/></div><b>{duration(choke.arrival_minutes)}</b></div>
    <div className="horizon-axis"><span>CLEARANCE</span><div className="horizon-track"><div className={`clearance-bar ${color}`}
      style={{width: `${clear / scale * 100}%`}}/><div className="uncertainty-bar" style={{left: `${clear / scale * 100}%`,
        width: `${(upper - clear) / scale * 100}%`}}/></div><b>{hazard ? duration(clear) : '—'}</b></div>
    <div className="horizon-data"><span>ALT {terrain?.altitude == null ? '—' : `${Math.round(terrain.altitude)}m`}</span>
      <span>SLOPE {terrain?.slope == null ? '—' : `${terrain.slope.toFixed(1)}°`}</span>
      <span>PCU {hazard?.golden_record.clearance?.pcu_queue_load?.toFixed(0) ?? '—'}</span></div>
    <div className="provider-readout"><span>{choke.telemetry_source || 'local_incident_clusters'}</span>
      {choke.live_speed_ratio != null && <b>{Math.round(choke.live_speed_ratio * 100)}% free-flow {choke.traffic_cache_hit ? '· cached' : ''}</b>}</div>
  </article>;
}
