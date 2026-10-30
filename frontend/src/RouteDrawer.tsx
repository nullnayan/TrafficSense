// @file: frontend/src/RouteDrawer.tsx
// @exports: RouteDrawer
// @depends_on: api.ts, eta.ts, Horizon.tsx, types.ts
// @purpose: Floating route instrument with corridor presets, realistic driving/hold math and clearance horizon.
import {useState} from 'react';
import {api, duration, localTime} from './api';
import {journeyTiming} from './eta';
import Horizon from './Horizon';
import type {GoldenRecord, ScoutResult, TerrainPoint} from './types';
export default function RouteDrawer({route, setRoute, select, terrain}: {route: ScoutResult | null;
  setRoute: (route: ScoutResult) => void; select: (record: GoldenRecord) => void; terrain: Record<string, TerrainPoint>}) {
  const [origin, setOrigin] = useState('Clock Tower Dehradun');
  const [destination, setDestination] = useState('Laxman Jhula Rishikesh');
  const [fixtures, setFixtures] = useState(true);
  const [depart, setDepart] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [collapsed, setCollapsed] = useState(window.innerWidth < 900);
  async function evaluate(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {setRoute(await api<ScoutResult>('/api/v1/scout/evaluate', {origin, destination,
      departure_at: new Date(Date.now() + depart * 60000).toISOString(), include_fixtures: fixtures, max_choke_points: 5}));}
    catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  const timing = route ? journeyTiming(route) : null;
  return <aside className={`right-hud glass ${collapsed ? 'collapsed' : ''}`}><div className="instrument-heading">
    <div><small>ROUTE SCOUT / CHOKE HORIZON</small><h2>Corridor assessment</h2></div>
    <button aria-label={collapsed ? 'Expand Route Scout' : 'Collapse Route Scout'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? '+' : '−'}</button></div>
    {!collapsed && <div className="route-body"><div className="preset-corridors">
      {[['NH-7', 'Clock Tower Dehradun', 'Laxman Jhula Rishikesh'], ['Devprayag', 'Har Ki Pauri Haridwar', 'Devprayag'],
        ['Mussoorie', 'Clock Tower Dehradun', 'Mussoorie']].map(([label, from, to]) => <button key={label}
          onClick={() => {setOrigin(from); setDestination(to);}}>{label} ↗</button>)}</div>
      <form onSubmit={evaluate}><label>ORIGIN<input required value={origin} onChange={e => setOrigin(e.target.value)}/></label>
        <label>DESTINATION<input required value={destination} onChange={e => setDestination(e.target.value)}/></label>
        <div className="departure-pills"><span>DEPART</span>{[0, 30, 120].map(m => <button type="button" key={m} aria-pressed={depart === m}
          onClick={() => setDepart(m)}>{m === 0 ? 'Now' : `+${duration(m)}`}</button>)}</div>
        <div className="scope-control"><button type="button" aria-pressed={fixtures} onClick={() => setFixtures(!fixtures)}>
          {fixtures ? 'Fixtures included' : 'Live records only'}</button><small>MAX 05 SAMPLES</small></div>
        <button className="primary wide" disabled={busy}>{busy ? 'Evaluating…' : 'Evaluate route →'}</button></form>
      {error && <p className="error" role="alert">{error}</p>}
      {route && timing ? <><div className="route-summary"><div><strong>{(route.distance_meters / 1000).toFixed(1)} km</strong>
        <span className={`status-tag ${timing.hold ? 'red' : 'green'}`}>{timing.hold ? 'DEPARTURE HOLD' : route.risk_level}</span></div>
        <p className="eta-equation"><b>{duration(timing.base)}</b> base + <b>{duration(timing.delay)}</b> modeled delay
          <br/>= <strong>{duration(timing.drive)} driving</strong></p>
        {timing.hold && <p className="hold-notice">Road blocked at arrival. Additional clearance wait: <b>{duration(timing.wait)}</b>.
          This is a departure hold, not driving time.</p>}
        <div className="route-source">{route.route_source}</div><small>{route.route_source === 'corridor_waypoint_fallback' ?
          'APPROXIMATE WAYPOINT GEOMETRY / VERIFY ROADS' : 'LIVE OSRM ROAD GEOMETRY'}</small>
        <details><summary>Arrival and waiting assumptions</summary><p>Baseline arrival {localTime(route.baseline_eta)}.
          Total elapsed including hold {duration(timing.elapsed)}; modeled arrival {localTime(route.adjusted_eta)}.
          Upstream delays propagate into downstream incident arrivals.</p></details></div>
        <div className="instrument-heading"><small>TOP {route.choke_points.length} / ARRIVAL × CLEARANCE</small></div>
        {route.choke_points.map(c => <Horizon key={c.choke_id} choke={c} route={route} terrain={terrain[c.choke_id]}/>)}
        {route.incidents.map(h => <button className="hazard-link" key={h.cluster_id} onClick={() => select(h.golden_record)}>
          <span className={`status-tag ${h.overlap === 'BLOCKED_AT_ETA' ? 'red' : h.overlap === 'RESIDUAL_RISK' ? 'amber' : 'green'}`}>{h.overlap}</span>
          <strong>{h.golden_record.landmark_tuple.primary_landmark}</strong><span>Inspect tower ↗</span></button>)}</> :
        <p className="scope-note empty">Select a corridor to project commuter arrival against incident clearance. Heights show PCU/severity; they are analytical glyphs.</p>}
    </div>}</aside>;
}
