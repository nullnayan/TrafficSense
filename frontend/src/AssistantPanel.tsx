// @file: frontend/src/AssistantPanel.tsx
// @exports: AssistantPanel
// @depends_on: api.ts, types.ts, /api/v1/assistant/query
// @purpose: Natural-language allowlisted SQL query, bound SlotFillingQuery evidence and table results.
import {useEffect, useState} from 'react';
import {api} from './api';
import type {QueryResult} from './types';
export default function AssistantPanel({initialQuery, close}: {initialQuery: string; close: () => void}) {
  const [query, setQuery] = useState(initialQuery);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {setQuery(initialQuery); setResult(null);}, [initialQuery]);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {setResult(await api<QueryResult>('/api/v1/assistant/query', {query, include_fixtures: true}));}
    catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  const columns = result ? Array.from(new Set(result.results.flatMap(row => Object.keys(row)))) : [];
  return <section className="assistant-panel"><div className="panel-head"><div><small>BOUND SLOTS · READ ONLY</small>
    <h2>Safe SQL Assistant</h2></div><button className="icon-button" onClick={close} aria-label="Close assistant">×</button></div>
    <p className="muted">Ask about active incidents, nearby hazards, corridor history or choke points.</p>
    <div className="quick-queries">{['Are there active incidents near Clock Tower?', 'Show corridor history for NH-7',
      'Show choke points on NH-7'].map((example, i) => <button key={example} onClick={() => setQuery(example)}>
        {['Nearby incidents', 'Corridor history', 'Choke points'][i]}</button>)}</div>
    <form onSubmit={submit}><label>Your question<textarea required maxLength={2000} rows={4} value={query}
      onChange={e => setQuery(e.target.value)}/></label><button className="primary wide" disabled={busy}>
      {busy ? 'Binding query…' : 'Run safe query ↗'}</button></form>
    {error && <p className="error" role="alert">{error}</p>}
    {result && <><h3>{result.row_count} result rows</h3><p>{result.explanation}</p>
      <span className="badge green">{result.template_id}</span> <span className="badge">{result.slot_source}</span>
      <small className="block-label">SLOTFILLINGQUERY · ALLOWLISTED TEMPLATE</small>
      <pre>{JSON.stringify({intent: result.template_id, sql_template_id: result.template_id, slots: result.parameters}, null, 2)}</pre>
      <p className="muted">Scope: {result.include_fixtures ? 'Includes synthetic fixtures' : 'Live only'}</p>
      {columns.length ? <div className="table-scroll"><table><thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{result.results.map((row, i) => <tr key={i}>{columns.map(c => <td key={c}>
          {typeof row[c] === 'object' ? JSON.stringify(row[c]) : String(row[c] ?? '—')}</td>)}</tr>)}</tbody></table></div> :
        <p className="empty">No rows match the bound parameters.</p>}</>}
  </section>;
}
