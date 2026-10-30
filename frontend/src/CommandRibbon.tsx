// @file: frontend/src/CommandRibbon.tsx
// @exports: CommandRibbon
// @depends_on: FilterPill.tsx, types.ts, hud.css
// @purpose: Compact floating operational ribbon with inline telemetry, themes, filters and camera modes.
import FilterPill from './FilterPill';
import type {GoldenRecord, Theme} from './types';
type Props = {records: GoldenRecord[]; connection: string; theme: Theme; setTheme: (value: Theme) => void;
  category: string; severity: string; setCategory: (v: string) => void; setSeverity: (v: string) => void;
  ortho: boolean; setOrtho: (v: boolean) => void; orbit: boolean; setOrbit: (v: boolean) => void;
  replay: () => void; assistant: () => void; camera: (ortho: boolean) => void};
export default function CommandRibbon(p: Props) {
  const themes: [Theme, string][] = [['civic_white_cad', '01 Civic White CAD'],
    ['highway_paper_blueprint', '02 Highway Paper Blueprint'], ['asphalt_carbon_radar', '03 Asphalt Carbon Radar']];
  return <header className="command-ribbon glass"><div className="ribbon-brand"><span className="emblem">T↗</span><strong>TrafficSense</strong></div>
    <div className="inline-telemetry"><span>CLUSTERS <b>{String(p.records.length).padStart(2, '0')}</b></span>
      <span>SEVERE <b>{String(p.records.filter(r => ['severe', 'catastrophic'].includes(r.severity)).length).padStart(2, '0')}</b></span>
      <span>REPORTS <b>{String(p.records.reduce((n, r) => n + r.report_count, 0)).padStart(2, '0')}</b></span>
      <span title="Recorded 34-place warm SQL benchmark; not live telemetry">SQL REF <b>2.59ms</b></span>
      <span className="bus"><i/> BUS <b>{p.connection === 'Connected' ? 'LIVE' : p.connection.toUpperCase()}</b></span></div>
    <nav className="look-switch" aria-label="Visual themes">{themes.map(([id, label]) => <button key={id}
      aria-pressed={p.theme === id} onClick={() => {p.setTheme(id); p.setOrtho(false);}}>{label}</button>)}</nav>
    <div className="ribbon-actions"><FilterPill label="Category" value={p.category} change={p.setCategory}
      options={Array.from(new Set(p.records.map(r => r.incident_type)))}/>
      <FilterPill label="Severity" value={p.severity} change={p.setSeverity} options={['minor', 'moderate', 'severe', 'catastrophic', 'unknown']}/>
      <button onClick={p.replay}>↻ Replay Burst</button><button className="accent-button" onClick={p.assistant}>⌘ Ask SQL Intelligence</button></div>
    <div className="camera-pill glass"><button aria-pressed={p.ortho} onClick={() => p.camera(true)}>2D Ortho</button>
      <button aria-pressed={!p.ortho} onClick={() => p.camera(false)}>3D Isometric 60°</button>
      <button aria-pressed={p.orbit} onClick={() => p.setOrbit(!p.orbit)}>◎ Orbit</button></div>
  </header>;
}
