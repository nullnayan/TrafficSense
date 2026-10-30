// @file: frontend/src/LeftDrawer.tsx
// @exports: LeftDrawer
// @depends_on: Inspector, AssistantPanel, PipelineBar, types.ts
// @purpose: Compact floating incident/SQL instrument with collapsible feed and canonical record inspection.
import Inspector from './Inspector';
import AssistantPanel from './AssistantPanel';
import PipelineBar from './PipelineBar';
import type {GoldenRecord} from './types';
export default function LeftDrawer({records, selected, select, close, refresh, tab, setTab, query, setQuery, ticker}: {
  records: GoldenRecord[]; selected: GoldenRecord | null; select: (record: GoldenRecord) => void;
  close: () => void; refresh: () => Promise<void>; tab: 'feed' | 'sql' | 'closed';
  setTab: (tab: 'feed' | 'sql' | 'closed') => void; query: string; setQuery: (query: string) => void; ticker: string[]}) {
  if (tab === 'closed') return <button className="drawer-reopen glass" onClick={() => setTab('feed')}>▤ Incident instruments</button>;
  return <aside className="left-hud glass"><div className="drawer-tabs"><button aria-pressed={tab === 'feed'} onClick={() => setTab('feed')}>01 Incident feed</button>
    <button aria-pressed={tab === 'sql'} onClick={() => setTab('sql')}>02 SQL intelligence</button>
    <button aria-label="Collapse left drawer" onClick={() => setTab('closed')}>−</button></div>
    <div className="drawer-body">{tab === 'sql' ? <AssistantPanel initialQuery={query} close={() => setTab('feed')}/> :
      selected ? <Inspector record={selected} close={close} history={corridor => {setQuery(`Show corridor history for ${corridor}`); setTab('sql');}}/> :
      <><div className="instrument-heading"><span>CANONICAL REGISTER</span><button onClick={() => void refresh()}>↻</button></div>
        <p className="scope-note">FIXTURES INCLUDED · SOURCE EVIDENCE RETAINED</p>
        {records.length ? records.map((record, index) => <button className="feed-record" key={record.cluster_id} onClick={() => select(record)}>
          <span className="record-index">{String(index + 1).padStart(2, '0')}</span><div><strong>{record.landmark_tuple.primary_landmark || record.incident_type}</strong>
            <small>{record.incident_type} / {record.landmark_tuple.corridor_name || 'Unstated corridor'}</small>
            <span className={`status-tag ${['severe', 'catastrophic'].includes(record.severity) ? 'amber' : 'green'}`}>{record.severity}</span>
            <small>{record.report_count} reports · {record.lanes_blocked === null ? 'lane count abstained' : `${record.lanes_blocked} lanes blocked`}</small></div><span>↗</span></button>) :
          <p className="empty">No active canonical records in this window. Run the fixture pipeline to populate the scene.</p>}
        <div className="stream-log"><small>REDIS / INCIDENT FAN-OUT</small>{ticker.slice(0, 4).map((line, i) => <p key={i}><i/> {line}</p>)}</div></>}
    </div><PipelineBar refresh={refresh}/></aside>;
}
