// @file: frontend/src/useIncidents.ts
// @exports: useIncidents
// @depends_on: api.ts, types.ts, /api/v1/events/stream
// @purpose: Snapshot and resumable live updates with supersession, deletion and ticker telemetry.
import {useCallback, useEffect, useRef, useState} from 'react';
import {api} from './api';
import type {GoldenRecord, StreamEnvelope} from './types';
export function useIncidents() {
  const [records, setRecords] = useState<GoldenRecord[]>([]);
  const [connection, setConnection] = useState('Replaying');
  const [ticker, setTicker] = useState<string[]>([]);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const version = ++generation.current;
    try {
      const next = await api<GoldenRecord[]>('/api/v1/incidents/active?include_fixtures=true');
      if (version === generation.current) {setRecords(next); setError('');}
    } catch (e) {setError((e as Error).message);}
  }, []);
  useEffect(() => {
    const source = new EventSource('/api/v1/events/stream?include_fixtures=true&replay_active=true');
    let snapshotStarted = false;
    source.addEventListener('ready', e => {
      const status = JSON.parse((e as MessageEvent).data);
      setConnection(status.replaying ? 'Replaying' : 'Connected');
      if (status.replaying) {snapshotStarted = true; generation.current++; setRecords([]);}
    });
    const update = (e: Event) => {
      const payload = JSON.parse((e as MessageEvent).data) as StreamEnvelope;
      const record = payload.golden_record;
      if (!payload.snapshot) generation.current++;
      setRecords(current => {
        const retained = current.filter(r => r.cluster_id !== record.cluster_id &&
          !payload.superseded_cluster_ids.includes(r.cluster_id));
        const prior = current.find(r => r.cluster_id === record.cluster_id);
        if (prior && prior.last_updated_at > record.last_updated_at) return current;
        // Geocoded envelopes can precede analyzed data for the same revision.
        if (prior && !record.clearance && prior.clearance && prior.last_updated_at === record.last_updated_at)
          return current;
        return record.is_active ? [...retained, record] : retained;
      });
      setTicker(current => [`${e.type.replace('incident_', '')} · ${record.landmark_tuple.primary_landmark || record.incident_type}`, ...current].slice(0, 8));
    };
    source.addEventListener('incident_active', update);
    source.addEventListener('incident_analyzed', update);
    source.onerror = () => setConnection('Reconnecting');
    // Poll reconciles lifecycle expiry, which currently has no deletion stream frame.
    const timer = window.setInterval(() => {if (!snapshotStarted || source.readyState === EventSource.OPEN) void refresh();}, 30000);
    void refresh();
    return () => {source.close(); clearInterval(timer); generation.current++;};
  }, [refresh]);
  return {records, connection, ticker, error, refresh};
}
