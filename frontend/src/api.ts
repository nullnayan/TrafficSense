// @file: frontend/src/api.ts
// @exports: api, minute, localTime
// @depends_on: frontend/vite.config.ts, backend API
// @purpose: Same-origin JSON API client with bounded requests and readable errors.
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(120000)});
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail));
  return payload as T;
}
export const minute = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(1)} min`;
export const duration = (value: number) => {
  const minutes = Math.max(0, Math.round(value));
  return minutes >= 120 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes} min`;
};
export const localTime = (value: string | null | undefined) => value ? new Date(value).toLocaleString('en-IN',
  {timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'}) : '—';
