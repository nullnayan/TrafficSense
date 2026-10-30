// @file: frontend/src/App.tsx
// @exports: App
// @depends_on: DigitalTwinMap, CommandRibbon, LeftDrawer, RouteDrawer, Timeline, useIncidents
// @purpose: Full-screen geospatial digital twin with floating CAD/highway/carbon instruments.
import {useCallback, useEffect, useState} from 'react';
import DigitalTwinMap from './DigitalTwinMap';
import CommandRibbon from './CommandRibbon';
import LeftDrawer from './LeftDrawer';
import RouteDrawer from './RouteDrawer';
import Timeline from './Timeline';
import {useIncidents} from './useIncidents';
import type {Cursor, GoldenRecord, ScoutResult, TerrainPoint, Theme} from './types';
export default function App() {
  const {records, connection, ticker, error, refresh} = useIncidents();
  const [theme, setTheme] = useState<Theme>('civic_white_cad');
  const [ortho, setOrtho] = useState(false), [orbit, setOrbit] = useState(false), [heatmap, setHeatmap] = useState(false);
  const [cameraTick, setCameraTick] = useState(0);
  const [category, setCategory] = useState('all'), [severity, setSeverity] = useState('all');
  const [hours, setHours] = useState(24), [offset, setOffset] = useState(0), [playing, setPlaying] = useState(false);
  const [tab, setTab] = useState<'feed' | 'sql' | 'closed'>(window.innerWidth < 900 ? 'closed' : 'feed');
  const [query, setQuery] = useState('Are there active incidents near Clock Tower?');
  const [selected, setSelected] = useState<GoldenRecord | null>(null), [route, setRoute] = useState<ScoutResult | null>(null);
  const [terrain, setTerrain] = useState<Record<string, TerrainPoint>>({}), [mapStatus, setMapStatus] = useState('LOADING VECTOR / DEM');
  const [cursor, setCursor] = useState<Cursor>({longitude: 78.08, latitude: 30.22, altitude: null});
  const end = Date.now() - offset * 3600000;
  const visible = records.filter(r => (category === 'all' || r.incident_type === category) && (severity === 'all' || r.severity === severity) &&
    new Date(r.last_updated_at).getTime() <= end && new Date(r.last_updated_at).getTime() >= end - hours * 3600000);
  const select = useCallback((record: GoldenRecord) => {setSelected(record); setTab('feed'); setOrbit(false);}, []);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setOffset(value => {if (value <= 0) {setPlaying(false); return 0;} return value - 1;}), 900);
    return () => clearInterval(timer);
  }, [playing]);
  const play = () => {if (!playing && offset === 0) setOffset(hours); setPlaying(!playing);};
  return <div className={`twin-console ${theme}`}><DigitalTwinMap records={visible} route={route} selected={selected}
    theme={theme} ortho={ortho} orbit={orbit} cameraTick={cameraTick} heatmap={heatmap} select={select} cursor={setCursor} terrain={setTerrain} status={setMapStatus}/>
    <div className="technical-grid" aria-hidden="true"/>
    <CommandRibbon records={records} connection={connection} theme={theme} setTheme={setTheme} category={category} severity={severity}
      setCategory={setCategory} setSeverity={setSeverity} ortho={ortho} setOrtho={setOrtho} orbit={orbit} setOrbit={setOrbit}
      replay={play} assistant={() => setTab('sql')} camera={value => {setOrtho(value); setCameraTick(n => n + 1);}}/>
    <LeftDrawer records={visible} selected={selected ? records.find(r => r.cluster_id === selected.cluster_id) || selected : null}
      select={select} close={() => setSelected(null)} refresh={refresh} tab={tab} setTab={setTab} query={query} setQuery={setQuery} ticker={ticker}/>
    <RouteDrawer route={route} setRoute={setRoute} select={select} terrain={terrain}/>
    <div className="scene-status glass"><span>{mapStatus}</span><button aria-pressed={heatmap} onClick={() => setHeatmap(!heatmap)}>Density</button>
      <small>500m DBSCAN / ANALYTICAL HEIGHTS</small>{error && <p role="alert" className="error">{error}</p>}</div>
    <Timeline hours={hours} offset={offset} setHours={setHours} setOffset={setOffset} playing={playing} play={play} cursor={cursor}/>
    <div className="map-credit">© <a href="https://openfreemap.org/">OpenFreeMap</a> · <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · AWS/Mapzen DEM</div>
  </div>;
}
