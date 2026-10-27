// The dashboard is a thin client: it fetches and renders, it never computes
// durations or aggregates. The service owns the data.

// In dev, Vite serves the UI on 5173 while the API stays on 8080. In
// production the service itself serves this bundle from 8080, so the API is
// same-origin and the base is empty — no CORS, and the dashboard works on
// whatever host the service is reached through.
const API_BASE = import.meta.env.DEV ? 'http://127.0.0.1:8080' : '';

async function get(path) {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function del(path, body) {
  const r = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function patch(path, body) {
  const r = await fetch(`${API_BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  health: () => get('/health'),
  rss: () => get('/health/rss'),

  // `since` is an ISO timestamp; the service returns events at or after it.
  events: (since) => get(`/events?since=${encodeURIComponent(since)}`),

  sessions: (date) => get(`/sessions?date=${date}`),

  // Soft delete: the row survives with a reason, recorded in the audit log.
  deleteSession: (id, actor, reason) =>
    del(`/sessions/${id}`, { actor, reason }),

  // Re-tag a trip. 'delivery' minutes are work time and exempt from the
  // daily out allowance; 'break' minutes count against it.
  setSessionPurpose: (id, purpose, actor = 'owner') =>
    patch(`/sessions/${id}`, { purpose, actor }),

  // role: 'staff' | 'delivery'; allowed_out_minutes: number or null (inherit).
  updateEmployee: (id, body) => patch(`/employees/${id}`, body),

  dailyReport: (date) => get(`/report/daily?date=${date}`),
  weeklyReport: (end, days = 7) => get(`/report/weekly?end=${end}&days=${days}`),
  reportCsvUrl: (date) => `${API_BASE}/report/daily.csv?date=${date}`,

  // WhatsApp link state for the header chip: `{enabled: false}` when
  // notifications are off, otherwise `{enabled, reachable, bridge}`.
  notifyStatus: () => get('/notify/status'),
  // The pairing QR (PNG) is fetched by URL, not through get() — it's an image.
  notifyQrUrl: () => `${API_BASE}/notify/qr`,

  today: () => new Date().toISOString().slice(0, 10),
};
