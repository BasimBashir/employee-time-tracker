// The dashboard is a thin client: it fetches and renders, it never computes
// durations or aggregates. The service owns the data.

const API_BASE = 'http://127.0.0.1:8080';

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

export const api = {
  health: () => get('/health'),
  rss: () => get('/health/rss'),

  // `since` is an ISO timestamp; the service returns events at or after it.
  events: (since) => get(`/events?since=${encodeURIComponent(since)}`),

  sessions: (date) => get(`/sessions?date=${date}`),

  // Soft delete: the row survives with a reason, recorded in the audit log.
  deleteSession: (id, actor, reason) =>
    del(`/sessions/${id}`, { actor, reason }),

  today: () => new Date().toISOString().slice(0, 10),
};
