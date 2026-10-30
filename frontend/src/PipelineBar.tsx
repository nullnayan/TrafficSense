// @file: frontend/src/PipelineBar.tsx
// @exports: PipelineBar
// @depends_on: api.ts, pipeline and stage endpoints
// @purpose: One-action viva orchestration plus individual stage controls and measured telemetry.
import {useState} from 'react';
import {api} from './api';
const stages = [['Ingest', '/ingest/trigger'], ['NLP', '/nlp/process'],
  ['Spatial', '/spatial/process'], ['Analytics', '/analytics/process']];
export default function PipelineBar({refresh}: {refresh: () => Promise<void>}) {
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState('');
  async function run(name: string, path: string) {
    setBusy(name); setError('');
    try {setResult(await api(`/api/v1${path}`, {})); await refresh();}
    catch (e) {setError((e as Error).message);} finally {setBusy('');}
  }
  return <footer className="pipeline-bar"><div className="pipeline-actions"><div><small>VIVA CONTROL</small>
    <span>Full pipeline, one action.</span></div><button className="primary" disabled={!!busy}
      onClick={() => void run('Demo', '/pipeline/run-demo')}>{busy === 'Demo' ? 'Pipeline running…' : '▶ Run full fixture pipeline'}</button>
    <div className="stage-buttons">{stages.map(([name, path]) => <button key={name} disabled={!!busy}
      onClick={() => void run(name, path)}>{busy === name ? `${name}…` : name}</button>)}</div>
    {result !== null && <details className="pipeline-result"><summary>Stage telemetry</summary>
      <pre>{JSON.stringify(result, null, 2)}</pre></details>}</div>
    {error && <p className="error" role="alert">{error}</p>}</footer>;
}
