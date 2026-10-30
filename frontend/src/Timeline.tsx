// @file: frontend/src/Timeline.tsx
// @exports: Timeline
// @depends_on: types.ts, hud.css
// @purpose: Custom accessible timeline track, bounded replay and honest DEM cursor readout.
import type {Cursor} from './types';
export default function Timeline({hours, offset, setHours, setOffset, playing, play, cursor}: {
  hours: number; offset: number; setHours: (n: number) => void; setOffset: (n: number) => void;
  playing: boolean; play: () => void; cursor: Cursor}) {
  const scrub = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setOffset(Math.round(Math.max(0, Math.min(hours, (1 - (e.clientX - rect.left) / rect.width) * hours))));
  };
  return <div className="timeline-pill glass"><div className="timeline-controls"><button onClick={play}
    aria-label={playing ? 'Pause replay' : 'Play replay'}>{playing ? 'Ⅱ' : '▶'}</button>
    <span className="timeline-time">{offset ? `T − ${offset}H` : 'LIVE'}</span>
    <div className="scrub-track" role="slider" tabIndex={0} aria-label="Timeline hours before now"
      aria-valuemin={0} aria-valuemax={hours} aria-valuenow={offset} onPointerDown={e => {e.currentTarget.setPointerCapture(e.pointerId); scrub(e);}}
      onPointerMove={e => {if (e.currentTarget.hasPointerCapture(e.pointerId)) scrub(e);}}
      onKeyDown={e => {if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {e.preventDefault();
        setOffset(e.key === 'Home' ? hours : e.key === 'End' ? 0 : Math.max(0, Math.min(hours, offset + (e.key === 'ArrowLeft' ? 1 : -1))));}}}>
      <span className="scrub-fill" style={{width: `${(1 - offset / hours) * 100}%`}}/>
      <i style={{left: `${(1 - offset / hours) * 100}%`}}/></div>
    <div className="window-pills">{[6, 24, 168].map(h => <button key={h} aria-pressed={hours === h}
      onClick={() => {setHours(h); setOffset(Math.min(offset, h));}}>{h === 168 ? '7d' : `${h}h`}</button>)}</div></div>
    <div className="cursor-readout">{cursor.latitude.toFixed(4)}° N, {cursor.longitude.toFixed(4)}° E
      <span>// ALT {cursor.altitude === null ? 'DEM PENDING' : `${Math.round(cursor.altitude)}m`}</span>
      <small>ACTIVE SNAPSHOT · UPDATE-TIME FILTER</small></div></div>;
}
